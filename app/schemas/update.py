"""应用更新检查的数据模型"""

from typing import Literal, Optional

from pydantic import BaseModel


class UpdateDownloadDirUpdate(BaseModel):
    """保存「安装包下载目录」的请求；空串 = 清除配置（回到未配置态）"""

    download_dir: str = ""


class UpdateDownloadDirOut(BaseModel):
    """「安装包下载目录」配置（关于卡片展示/编辑用）

    download_dir 对管理员回显完整路径（配置与排障需要），对普通账号只回显
    目录名 —— 服务器目录布局属于内部信息，与 NAS 账单目录同策略。
    """

    download_dir: str = ""
    # 是否已配置（未配置时下载接口拒绝并提示先配置）
    configured: bool = False
    # 已配置的目录当前是否存在/可访问（前端据此提示）
    exists: bool = False


class UpdateDownloadRequest(BaseModel):
    """「下载最新安装包到 NAS」的请求参数（语义与 check 接口的同名参数一致）"""

    # 发布站点：github（默认）/ gitee
    source: Literal["gitee", "github"] = "github"
    # 版本渠道：release=正式版 / dev=开发版；缺省=跟随本机版本渠道
    channel: Optional[Literal["release", "dev"]] = None


class UpdateDownloadResult(BaseModel):
    """下载最新安装包到 NAS 的结果

    与 UpdateCheckResult 同策略：失败**不抛错**，用 ok=False + message 表达，
    界面内联展示原因（离线、未授权目录、无 fpk 附件都是「办不成」，不是异常）。
    """

    ok: bool = True
    # 一句话结论：成功时是「已下载 + 校验情况」，失败时是原因
    message: str = ""
    # 下载的版本号（来自 Release tag）
    version: str = ""
    # 落盘文件名（优先带版本号副本的附件名，如 fn-finstat-v1.1.2.fpk）
    file_name: str = ""
    # 保存位置：目录 + 完整路径（前端原样展示，方便用户去文件管理器找）
    dest_dir: str = ""
    saved_path: str = ""
    # 文件大小（字节）
    size: int = 0
    # MD5 校验情况：True=有校验文件且匹配；False=无校验文件（无法校验）。
    # 校验不匹配时不 ok=False 并删除落盘文件，不会带着 False 返回成功
    md5_verified: bool = False
    source: Literal["gitee", "github"] = "github"
    channel: Literal["release", "dev"] = "release"


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
    # 本次检查比对的版本渠道：显式选择（release=只看正式版 / dev=测试版），
    # 缺省时由本机版本号推导（含预发布段即 dev）
    channel: Literal["release", "dev"] = "release"
    # 本次检查使用的发布站点：github（默认）/ gitee。两条流水线对同一 tag
    # 在两个站点各发一份 Release，附件与说明同源，任一站点检查到的结论一致
    source: Literal["gitee", "github"] = "github"
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
