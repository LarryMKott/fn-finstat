"""NAS 用户授权目录：trim 网关可用性 + 不可用降级统一封装

设计目标：
- 「不影响其他功能」 —— 网络/协议/scope 一切失败都降级到本地配置，不抛
- 「失败时明确错误」 —— 不抛 5xx；返回值携带 `available`、`reason`、`folders` 三段
  给前端做精细展示
- 「不引入额外写入路径」 —— 旧 `update_config` 与 `load_nas_settings` 不动
- 「本地/独立部署场景优雅退化」 —— `is_trim_runtime()` 为 False 时返回 `available=False`，
  调用方能识别"此环境没有用户授权能力"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from app.config import APP_NAME, load_nas_settings
from app.core.context import GatewayUser
from app.services import trim_gateway

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UserAuthorizationStatus:
    """当前用户账单目录授权状态（前端导入页展示用）

    字段语义：
    - `available`: 当前进程是否在飞牛 fnOS 环境且具备 trim 网关能力
    - `authorized`: 当前用户是否授权过至少一个目录
    - `folders`: 用户已授权的目录路径列表（available=False 时为空）
    - `reason`: unavailable / partially-fulfilled 的原因文案，前端可原样展示
    - `uid`: 当前用户映射到的数字 uid（available=True 时有值，否则为 0）
    """

    available: bool
    authorized: bool
    folders: list[str]
    reason: str
    uid: int

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "authorized": self.authorized,
            "folders": list(self.folders),
            "reason": self.reason,
            "uid": self.uid,
        }


def _err_reason(exc: Exception) -> str:
    """把 trim 异常归一为前端可直接展示的用户文案

    三类：
    - TrimGatewayUnavailable: 能力本身不可用（Socket/Token/超时/协议）
    - TrimGatewayRejected: 网关可达但权限不足（如 scope 未声明 / 参数错）
    - 其他: 容灾
    """
    if isinstance(exc, trim_gateway.TrimGatewayUnavailable):
        return f"飞牛授权通道不可用：{exc.msg}"
    if isinstance(exc, trim_gateway.TrimGatewayRejected):
        return f"飞牛授权调用被拒绝（code={exc.code}）：{exc.msg or '无错误消息'}"
    return f"获取用户授权目录失败：{exc}"


def get_user_authorization(user: GatewayUser) -> UserAuthorizationStatus:
    """查询当前用户的账单目录授权状态（飞牛环境）

    本地 / 独立部署场景（无 trim socket/token）返回 `available=False`；
    飞牛环境但 trim 调用失败时同样 `available=False` 并附 reason，前端能
    据此展示「请联系管理员 / 检查网关版本」之类的提示。
    """
    if not trim_gateway.is_trim_runtime():
        return UserAuthorizationStatus(
            available=False,
            authorized=False,
            folders=[],
            reason="当前运行环境不在飞牛 fnOS 中（缺少 socket 或 token），无法获取用户授权目录",
            uid=0,
        )

    uid = trim_gateway.uid_from_user_id(user.user_id or "")
    try:
        result = trim_gateway.get_user_accessible_folders(
            uid, app_name=APP_NAME
        )
    except (
        trim_gateway.TrimGatewayUnavailable,
        trim_gateway.TrimGatewayRejected,
    ) as exc:
        logger.warning("获取用户授权目录失败 uid=%s: %s", uid, exc)
        return UserAuthorizationStatus(
            available=False,
            authorized=False,
            folders=[],
            reason=_err_reason(exc),
            uid=uid,
        )
    except Exception as exc:  # 容灾：任何意外都不能阻塞导入主流程
        logger.exception("获取用户授权目录时遇到未预期异常：%s", exc)
        return UserAuthorizationStatus(
            available=False,
            authorized=False,
            folders=[],
            reason=_err_reason(exc),
            uid=uid,
        )

    return UserAuthorizationStatus(
        available=True,
        authorized=bool(result.paths),
        folders=list(result.paths),
        reason="" if result.paths else "尚未授权任何目录，请在弹窗中选一个目录授权给应用",
        uid=uid,
    )


def first_authorized_folder(user: GatewayUser) -> Optional[str]:
    """返回当前用户首个授权目录（无授权时返回 None）

    用于"飞牛场景下导入页默认展示当前用户的账单目录"。本地/未授权/失败
    均返回 None，由调用方按"再用旧 import_dir / 提示配置"等策略兜底。

    ⚠️ 当前无调用方：v0.5 只做到"能感知授权状态并提供申请入口"，尚未把
    "默认目录 = 用户授权目录"接进 NAS 导入配置。保留此函数作为下一批的挂接点，
    逻辑与测试都已就绪，届时直接接线即可（勿删后重写）。
    """
    status = get_user_authorization(user)
    if status.available and status.authorized:
        return status.folders[0]
    return None


def resolve_effective_import_dir(
    user: GatewayUser,
    *,
    prefer_user_authorized: bool = False,
) -> tuple[str, UserAuthorizationStatus]:
    """解析一个可在导入页展示的「账单目录」

    返回 (import_dir, authorization_status)。当 prefer_user_authorized=True 时，
    优先使用当前用户的首个授权目录；否则优先使用旧 `nas_config.json`；
    都没有时 `import_dir` 为空串、`authorization_status.reason` 给出提示。

    注意：本函数**不修改**配置存储，也不修改 `import_file` 流程，只是
    把"飞牛授权目录"作为一种"能感知但不改旧 data"的展示来源，
    与本地开发/独立部署完全兼容。

    ⚠️ 当前无调用方（同 `first_authorized_folder`）：保留作为下一批"导入页
    默认目录优先取用户授权目录"的挂接点。
    """
    status = get_user_authorization(user)
    legacy = load_nas_settings().import_dir

    candidate = ""
    if prefer_user_authorized and status.authorized:
        candidate = status.folders[0]
    elif legacy:
        candidate = legacy
    elif status.authorized:
        candidate = status.folders[0]

    return candidate, status


def _join_unix(root: str, rel: str) -> str:
    """把 (root, rel) 拼成"飞牛风格绝对路径"

    fnOS 是 Linux，路径用正斜杠；在 Windows 上 `Path('/vol1/...').resolve()`
    会塞上前缀盘符，破坏与网关两端的对齐。这里走纯字符串拼路径，确保：
      _join_unix('/vol1/1000/bills', 'a.csv') == '/vol1/1000/bills/a.csv'
      _join_unix('/vol1/1000/bills', '')      == '/vol1/1000/bills'
    """
    r = (root or "").rstrip("/")
    if not r:
        return rel
    if not rel:
        return r
    return f"{r}/{rel.strip('/')}".rstrip("/") or r


def check_path_acl(user: GatewayUser, rel_paths: list[str]) -> dict:
    """对若干账单目录内路径做 ACL 检查，返回 dict-of-paths-to-perms

    失败/不可用场景全部 fall-through 到"全部可读可写可删 True"的宽松
    语义：本地部署和飞牛未启用 scope 时，应用所在用户对其 home 目录有
    全权访问；飞牛环境权限收紧的语义才交给 trim。返回结构示例：

        {"foo.csv": {"readable": true, "writable": true, "deletable": true}}

    调用方可以按 `readable=false` 决定是否灰显某些行。
    """
    if not rel_paths:
        return {}
    if not trim_gateway.is_trim_runtime():
        # 宽松策略：未启用 trim 通道时，假设应用对自己的工作目录全权
        return {
            p: {"readable": True, "writable": True, "deletable": True}
            for p in rel_paths
        }

    uid = trim_gateway.uid_from_user_id(user.user_id or "")
    # 这里传绝对路径：ACL 检查是文件级语义，服务层需要先解析到 root
    root = load_nas_settings().import_dir
    abs_paths: list[str] = []
    path_to_rel: dict[str, str] = {}
    if root:
        for rel in rel_paths:
            ap = _join_unix(root, rel)
            abs_paths.append(ap)
            path_to_rel[ap] = rel

    if not abs_paths:
        return {}

    try:
        entries = trim_gateway.check_user_acl(uid, abs_paths, app_name=APP_NAME)
    except (
        trim_gateway.TrimGatewayUnavailable,
        trim_gateway.TrimGatewayRejected,
    ) as exc:
        logger.warning(
            "检查路径 ACL 失败，按宽松策略放行：%s",
            exc,
        )
        return {
            rel: {"readable": True, "writable": True, "deletable": True}
            for rel in rel_paths
        }
    except Exception as exc:  # 容灾
        logger.exception("检查路径 ACL 时遇到未预期异常，按宽松策略放行：%s", exc)
        return {
            rel: {"readable": True, "writable": True, "deletable": True}
            for rel in rel_paths
        }

    # 把返回的绝对路径映射回相对路径
    result: dict[str, dict] = {}
    matched_rels: set[str] = set()
    for entry in entries:
        rel = path_to_rel.get(entry.path)
        if rel is None:
            # 兜底：用 stripped 字串匹配
            for ap, rl in path_to_rel.items():
                if ap.endswith(entry.path) or entry.path.endswith(ap):
                    rel = rl
                    break
        if rel is None:
            continue
        result[rel] = {
            "readable": entry.readable,
            "writable": entry.writable,
            "deletable": entry.deletable,
        }
        matched_rels.add(rel)
    # 未在 trim 响应里出现的路径（罕见：路径不存在），默认读写删都 False，与文档约定一致
    for rel in rel_paths:
        if rel not in matched_rels:
            result[rel] = {"readable": False, "writable": False, "deletable": False}
    return result
