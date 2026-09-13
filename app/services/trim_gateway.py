"""飞牛开放平台后端能力客户端（trim.file.*）

通过 Unix Socket `/var/run/trim_open_gateway_apiscope.socket` 调用
`POST /api/v1/trimapp`，认证用环境变量 `TRIM_API_TOKEN`。

设计目标：
- **不可用即降级** —— Socket 不存在 / Token 缺失 / 调用超时 / 协议错误
  都不能影响主流程；统一抛 `TrimGatewayUnavailable`，调用方按"宁可不接
  权限校验也要继续"的策略处理
- **响应字段容错** —— `code` 不为 0 时归一为失败；message 透传给上层
- **每次调用现读 token** —— 文档明确 token 可能轮换，绝不缓存、绝不持久化
- **超时/重试边界明确** —— 单次 2 秒，失败即认输，避免上层接口被 trim 网关
  拖住

> 注：本模块不修改 GatewayUser。在飞牛场景下用 `user_id` 字符串稳定
> 派生出数字 uid（`crc32` 取低 31 位）作为目录授权查询的 uid；
> 本地开发场景 uid=0，由调用方决定是否要发起 trim 调用。
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
import uuid
import zlib
from dataclasses import dataclass
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

GATEWAY_SOCKET = "/var/run/trim_open_gateway_apiscope.socket"
GATEWAY_HTTP_PATH = "/api/v1/trimapp"
# 单次调用上限 2s —— 用户操作路径（设置页查询），不宜久等
DEFAULT_TIMEOUT_SEC = 2.0


class TrimGatewayUnavailable(Exception):
    """trim 网关不可用：Socket 不存在 / Token 缺失 / 超时 / 协议错误

    调用方应捕获并按"降级到本地策略"处理；不应向终端用户冒 5xx。
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.msg = message


class TrimGatewayRejected(Exception):
    """trim 网关返回非 0 业务码（权限不足 / scope 缺失 / 参数错等）

    携带远端 code 与 msg 供上层做诊断与前端提示使用。
    """

    def __init__(self, code: int, msg: str) -> None:
        super().__init__(f"trim.code={code} msg={msg!r}")
        self.code = code
        self.msg = msg


def uid_from_user_id(user_id: str) -> int:
    """把字符串 user_id 派生出稳定的正整数 uid（crc32 低 31 位）

    飞牛网关要求 `uid: number`；无 uid 时（本地开发 / 独立部署），
    调用方不要在飞牛场景下硬塞 0，应通过 `is_trim_runtime()` 守门。
    """
    digest = zlib.crc32(user_id.encode("utf-8")) & 0x7FFFFFFF
    # 用户名空时落到 1（避免 0 与"未登录"歧义），但保留 0 表示"系统用户"
    return digest or 1


def is_trim_runtime() -> bool:
    """判断当前进程是否在飞牛 fnOS 环境中（socket + token 同时可用）

    任何一个缺失就视为"无 trim"，调用方应走本地降级链路，不发起 HTTP 调用。
    """
    if not os.path.exists(GATEWAY_SOCKET):
        return False
    if not os.environ.get("TRIM_API_TOKEN"):
        return False
    return True


def _build_request(req: str, data: dict, app_name: str) -> bytes:
    """构造完整的 HTTP-over-Unix-Socket 请求字节（header + body）

    token 每次现读（文档明确会轮换），绝不缓存、绝不持久化。
    """
    token = os.environ["TRIM_API_TOKEN"]
    body = json.dumps(
        {
            "reqId": uuid.uuid4().hex,
            "req": req,
            "appName": app_name,
            "data": data,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    header = "\r\n".join(
        [
            f"POST {GATEWAY_HTTP_PATH} HTTP/1.1",
            "Host: localhost",
            "Content-Type: application/json",
            f"Authorization: Bearer {token}",
            f"Content-Length: {len(body)}",
            "Connection: close",
            "",
            "",
        ]
    ).encode("ascii")
    return header + body


def _exchange(request: bytes, req: str, timeout_sec: float) -> bytes:
    """连上 socket 发请求并读完响应，返回原始字节

    超时与网络异常都归一为 TrimGatewayUnavailable；socket 在 finally 里一定关闭。
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    chunks: list[bytes] = []
    try:
        sock.settimeout(timeout_sec)
        sock.connect(GATEWAY_SOCKET)
        sock.sendall(request)
        while True:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                raise TrimGatewayUnavailable(
                    f"trim {req} 超时（>{timeout_sec:.1f}s）",
                ) from None
            if not chunk:
                break
            chunks.append(chunk)
    except OSError as exc:
        raise TrimGatewayUnavailable(
            f"trim {req} 通信失败：{exc}",
        ) from exc
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return b"".join(chunks)


def _parse_response(raw: bytes, req: str) -> dict:
    """解析 trim 响应：剥离 HTTP header → 解 JSON → 校验业务码

    code != 0 抛 TrimGatewayRejected；协议/格式问题抛 TrimGatewayUnavailable。
    """
    if not raw:
        raise TrimGatewayUnavailable(f"trim {req} 返回空响应")
    header_end = raw.find(b"\r\n\r\n")
    if header_end < 0:
        raise TrimGatewayUnavailable(f"trim {req} 响应无 header 终止：{raw[:80]!r}")
    body = raw[header_end + 4 :]
    try:
        # 部分服务端会在 body 前留 BOM/空白
        body_text = body.lstrip(b"\xef\xbb\xbf \t\r\n").decode("utf-8")
        payload_json = json.loads(body_text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrimGatewayUnavailable(
            f"trim {req} 响应解析失败：{exc}; body_head={body[:80]!r}",
        ) from exc

    if not isinstance(payload_json, dict):
        raise TrimGatewayUnavailable(
            f"trim {req} 响应非 JSON object：{type(payload_json).__name__}",
        )

    code = payload_json.get("code", 0)
    msg = payload_json.get("msg", "") or ""
    if code != 0:
        raise TrimGatewayRejected(int(code), str(msg))

    data_out = payload_json.get("data")
    return data_out if isinstance(data_out, dict) else {}


def _request(
    req: str,
    data: dict,
    *,
    app_name: str,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
) -> dict:
    """HTTP-over-Unix-Socket 调用 trim 后端能力

    返回远端响应 data 字段。code != 0 抛 TrimGatewayRejected；网络/协议异常
    抛 TrimGatewayUnavailable。Socket 不存在或缺 token 时直接判不可用。
    """
    if not is_trim_runtime():
        raise TrimGatewayUnavailable(
            "trim 运行环境未就绪（socket/token 缺失）",
        )
    return _parse_response(
        _exchange(_build_request(req, data, app_name), req, timeout_sec),
        req,
    )


def _log_timing(req: str, started: float) -> None:
    duration = (time.monotonic() - started) * 1000
    # 慢于 500ms 打 warn，便于排查网关侧耗时
    if duration > 500:
        logger.warning("trim %s 耗时 %.0fms", req, duration)


# ---- 对外暴露：trim.file.* 业务封装 ----


@dataclass(frozen=True)
class UserAccessibleFolders:
    """当前用户授权给本应用的目录路径列表（来自 trim.file.getUserAccessibleFolders）"""

    paths: list[str]


def get_user_accessible_folders(uid: int, *, app_name: str) -> UserAccessibleFolders:
    """查询当前用户授权给本应用的目录列表

    trim 不可用 → TrimGatewayUnavailable；
    用户未授权任何目录时 `paths` 为空数组（属正常情况，不是错误）。
    """
    started = time.monotonic()
    try:
        out = _request(
            "trim.file.getUserAccessibleFolders",
            {"uid": int(uid)},
            app_name=app_name,
        )
    finally:
        _log_timing("trim.file.getUserAccessibleFolders", started)

    raw_paths = out.get("paths") if isinstance(out, dict) else None
    paths: list[str] = []
    if isinstance(raw_paths, list):
        for item in raw_paths:
            if isinstance(item, str) and item.strip():
                paths.append(item.strip())
    return UserAccessibleFolders(paths=paths)


@dataclass(frozen=True)
class AclEntry:
    """单个路径的权限判定结果（来自 trim.file.checkUserACL）"""

    path: str
    readable: bool
    writable: bool
    deletable: bool


def check_user_acl(
    uid: int, paths: Sequence[str], *, app_name: str
) -> list[AclEntry]:
    """批量检查当前用户对一组路径的可读/可写/可删权限

    任一路径在 trim 端不存在或应用无权读取权限状态时，该路径会返回三个
    权限均为 False 的结果（与官方文档约定一致）。函数永不抛 PermissionError：
    权限拒绝用结构化的 False 表达，让上层按"可见但只读"等策略继续。
    """
    normalized = [str(p).strip() for p in paths if str(p).strip()]
    if not normalized:
        return []
    started = time.monotonic()
    try:
        out = _request(
            "trim.file.checkUserACL",
            {"uid": int(uid), "path": normalized},
            app_name=app_name,
        )
    finally:
        _log_timing("trim.file.checkUserACL", started)

    raw_list = out.get("data") if isinstance(out, dict) else None
    # 文档示例响应是 data:[{path,readable,writable,deletable}, ...]
    if not isinstance(raw_list, list):
        # 容灾：极少数网关实现把列表嵌到 data.data 之类的嵌套 key，这里宽容接纳。
        # 命中条件 = 远端违反当前 trim 文档（正常分支就是 out["data"] 为 list），
        # 属防御性代码而非文档对齐；若官方明确废止嵌套响应，本分支可安全移除。
        raw_list = next(
            (v for v in out.values() if isinstance(v, list)),
            [] if isinstance(out, dict) else [],
        )

    entries: list[AclEntry] = []
    if isinstance(raw_list, list):
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            entries.append(
                AclEntry(
                    path=path,
                    readable=bool(item.get("readable")),
                    writable=bool(item.get("writable")),
                    deletable=bool(item.get("deletable")),
                )
            )
    return entries


def parse_first_req_id(text: str) -> Optional[str]:
    """容错地从 trim 响应中解析 reqId（仅供调试/日志使用）"""
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(obj, dict):
        rid = obj.get("reqId")
        return rid if isinstance(rid, str) else None
    return None
