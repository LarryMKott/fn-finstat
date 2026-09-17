"""应用更新检查的数据模型"""

from typing import Literal

from pydantic import BaseModel


class UpdateCheckResult(BaseModel):
    """检查更新的结果

    检查失败（无外网 / 接口限流 / 响应异常）**不抛错**，用 ok=False + message 表达，
    与 ConnectionTestResult 同策略：界面直接内联展示原因即可，不必弹错误浮层 ——
    「没连上网」是离线部署下的常态，不是用户操作失误。
    """

    ok: bool = True
    # 一句话结论：成功时是「已是最新版本 / 发现新版本 v0.8.0」，失败时是原因
    message: str = ""
    # 本机版本（含渠道派生段，dev 构建形如 0.7.1-dev.2.g8c2979a）
    current_version: str
    # 由本机版本号推导的发布渠道，决定与哪些 Release 比对
    channel: Literal["release", "dev"] = "release"
    # 远端比对结果（按渠道过滤后版本号最大的那条 Release）
    latest_version: str = ""
    # 远端版本与本机版本的关系：newer=有新版本 / same=已是最新 / older=远端更旧
    # （older 出现在本机装了比已发布版本更新的未发布构建时）
    relation: Literal["newer", "same", "older", "unknown"] = "unknown"
    release_name: str = ""
    published_at: str = ""
    # Release 正文摘录：只截**版本号对得上**的那一小节（Release 描述取自仓库
    # CHANGELOG.md，可能落后于构建；对不上时留空串，界面据此隐藏说明区块）
    notes: str = ""
    # 本次版本对应的 fpk 直链（优先带版本号副本，其次渠道别名/裸名）
    download_url: str = ""
    # MD5SUMS.txt 直链；该 Release 未附校验文件时为空串
    checksum_url: str = ""
    # Release 详情页（查看完整说明）
    page_url: str = ""
    checked_at: str = ""
    # 结果是否复用了后端进程内缓存（5 分钟内的重复检查不再次请求 Gitee）
    cached: bool = False
