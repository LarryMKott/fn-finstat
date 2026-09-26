"""出站请求目标地址安全校验（SSRF 防线）

背景：应用有两条「用户/管理员可配置目标地址」的出站通道 ——

1. 通知 Webhook（`services/notify_service.send_webhook`，管理员可配）
2. AI 服务 base_url（`api/ai.py` 保存配置，`services/ai_service` 出站）

两者原先都只校验 URL 以 `http(s)://` 开头，**没有对内网地址的限制**。后果：

- 可探测内网服务与端口（`http://127.0.0.1:6379/`、`http://10.0.0.1:3306/`）；
- 在云主机上可访问元数据服务（`http://169.254.169.254/...`、阿里云
  `http://100.100.100.200/...`），可能拿到实例凭证；
- AI 通道更严重：它会把 API Key 以 Bearer 头发给该地址，配置被恶意引导即泄露密钥。

本模块提供 `validate_outbound_url()`：解析 URL 的主机名并逐个解析其 IP，
拒绝全部「非公网」地址。零新增依赖（`ipaddress` + `socket` 均为标准库）。

设计取舍：
- **不做 URL 重写/白名单化**，只做拒绝：保持零依赖与行为可预期。
- **DNS 解析后再校验**：单看字面量无法拦住 `localtest.me` 这类解析到 127.0.0.1
  的公网域名（DNS rebinding 的常见手法）。
- **解析不出 IP 时拒绝（fail-closed）**：宁可让配置失败，也不放过未验证目标。
"""

import ipaddress
import socket
from urllib.parse import urlsplit

# 回环主机名（小写）：即便解析结果意外落在允许范围，也直接按名字拒绝
_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})

# 云厂商元数据服务的「魔法地址」——它们多为链路本地或厂商私有地址，
# 已在 IP 网段判定中覆盖，此处单独列出以便报错文案更明确、并防止将来
# 某个地址落在「看似公网」的段里被漏判。
CLOUD_METADATA_HOSTS = frozenset(
    {
        "169.254.169.254",  # AWS / Azure / OpenStack / 腾讯云等通用元数据
        "169.254.170.2",  # AWS ECS 任务角色
        "100.100.100.200",  # 阿里云元数据
        "metadata.google.internal",  # GCP（域名，解析后仍会被网段拦）
        "metadata.goog",
        "fd00:ec2::254",  # AWS IPv6 元数据
    }
)


class OutboundBlockedError(ValueError):
    """出站目标被安全策略拒绝（文案可直接展示给用户）"""


def _is_disallowed_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """IP 是否属于「不允许出站访问」的范围

    覆盖：回环、私网（RFC1918 及 IPv6 ULA）、链路本地（含云元数据所在段）、
    共享地址段（运营商 CGNAT，可能暴露内网）、保留/未指定/组播地址。

    注意 `is_private` 对 IPv6 ULA（fc00::/7）与 IPv4 私网均返回 True，
    但**不覆盖链路本地 169.254.0.0/16**（Python 中 `is_link_local` 单独判定）。
    """
    return (
        ip.is_private  # 10/8、172.16/12、192.168/16、fc00::/7 等
        or ip.is_loopback  # 127.0.0.0/8、::1
        or ip.is_link_local  # 169.254.0.0/16（云元数据主战场）、fe80::/10
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified  # 0.0.0.0、::
    )


def _resolve_ips(host: str) -> list[str]:
    """解析主机名到全部 IP（IPv4 + IPv6）；失败抛 OutboundBlockedError

    fail-closed：解析不出来的目标一律拒绝，避免「未验证即放行」。
    """
    # 已是字面量时不必查 DNS（也避免 localhost 解析受 hosts 文件影响）
    try:
        return [str(ipaddress.ip_address(host.strip("[]")))]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise OutboundBlockedError(
            f"无法解析目标主机 {host!r}，已拒绝该地址"
        ) from exc
    ips = {info[4][0] for info in infos if info[4]}
    if not ips:
        raise OutboundBlockedError(f"无法解析目标主机 {host!r}，已拒绝该地址")
    return sorted(ips)


def validate_outbound_url(url: str, *, allow_private: bool = False) -> str:
    """校验出站 URL 的目标地址，通过则原样返回，否则抛 OutboundBlockedError

    检查项：
    1. scheme 必须是 http / https（拒 file / gopher / dict / ftp 等）
    2. 必须有主机名
    3. 主机名解析出的**每一个** IP 都不得属于内网/回环/链路本地/云元数据范围
       （只要有一个落在禁止范围就整体拒绝，防「多 A 记录绕过」）

    allow_private：仅供测试与本机自检使用，生产调用不要传 True。
    """
    url = (url or "").strip()
    if not url:
        raise OutboundBlockedError("地址不能为空")

    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise OutboundBlockedError(f"地址格式不合法：{url!r}") from exc

    if parts.scheme not in ("http", "https"):
        raise OutboundBlockedError("地址必须以 http:// 或 https:// 开头")

    host = (parts.hostname or "").strip()
    if not host:
        raise OutboundBlockedError("地址缺少主机名")

    if host.lower() in _LOOPBACK_NAMES:
        raise OutboundBlockedError("不允许访问本机地址")

    if allow_private:
        return url

    if host.lower() in CLOUD_METADATA_HOSTS:
        raise OutboundBlockedError("不允许访问云服务元数据地址")

    for ip_str in _resolve_ips(host):
        try:
            ip = ipaddress.ip_address(ip_str.split("%")[0])  # 去掉 IPv6 zone id
        except ValueError:
            raise OutboundBlockedError(
                f"目标主机 {host!r} 解析出非法地址 {ip_str!r}，已拒绝"
            ) from None
        if _is_disallowed_ip(ip):
            raise OutboundBlockedError(
                f"不允许访问内网/本机/保留地址（{host} → {ip_str}）"
            )
    return url
