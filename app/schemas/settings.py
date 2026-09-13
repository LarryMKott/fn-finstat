"""设置接口的数据模型"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.config import DBSettings


class TargetDatabase(BaseModel):
    """设置页填写的目标数据库连接（迁移目的地，仅支持外部数据库）"""

    db_type: Literal["mysql", "postgresql"]
    host: str = "127.0.0.1"
    port: Optional[int] = None
    name: str = Field(min_length=1)
    user: str = "root"
    password: str = ""

    def to_settings(self) -> DBSettings:
        return DBSettings(
            db_type=self.db_type,
            host=self.host.strip() or "127.0.0.1",
            port=self.port or 0,
            name=self.name.strip(),
            user=self.user.strip() or "root",
            password=self.password,
        ).sanitized()


class DatabaseInfo(BaseModel):
    """当前生效的数据库信息（密码不回传，只给是否已设置的标记）"""

    db_type: str
    host: Optional[str] = None
    port: Optional[int] = None
    name: str
    user: Optional[str] = None
    has_password: bool = False
    sqlite_path: Optional[str] = None
    bills: int = 0
    categories: int = 0
    schema_version: int = 0
    schema_latest: int = 0
    # 当前飞牛账号（空串 = 本地/独立部署无网关身份）；user_name 便于界面展示
    user_id: str = ""
    user_name: Optional[str] = None
    # 升级前入库、尚未归属任何账号的历史流水数
    unassigned_bills: int = 0


class ConnectionTestResult(BaseModel):
    """连接测试结果：连不上时 ok=False、message 为原因（不抛错，便于界面直接展示）"""

    ok: bool
    message: str
    server_version: Optional[str] = None
    # None 表示目标库还没有业务表（全新库）；True/False 表示目标库是否已有数据
    target_empty: Optional[bool] = None


class MigrateResult(BaseModel):
    """迁移并切换的结果统计（merged=True 表示目标库非空、按唯一键去重合并）"""

    ok: bool = True
    message: str = ""
    source_bills: int
    source_categories: int
    copied_bills: int
    copied_categories: int
    # 目标库原本有数据时为去重合并
    merged: bool = False
    switched: bool = True


class UserClaimResult(BaseModel):
    """认领历史数据的结果"""

    claimed: int
    message: str = ""


class RuntimeLog(BaseModel):
    """运行日志尾部内容（设置页查看用；完整文件经 /logs/download 下载）"""

    path: str
    size: int
    truncated: bool
    lines: int
    content: str


class BackupRestoreResult(BaseModel):
    """备份恢复结果：各节实际提交的数据条数（合并模式含被唯一键去重的行），skipped 为格式非法跳过行数"""

    replaced: bool
    bills: int
    categories: int
    budgets: int
    assets: int
    skipped: int = 0


class AboutInfo(BaseModel):
    """应用「关于」信息：设置页底部展示"""

    app_name: str
    version: str
    author: str
    author_url: str
    repo_url: str
    description: str = ""
    # 网关透传的宿主主题（light/dark），未透传时为空串。
    # 用途：iframe 跨域时前端读不到飞牛的 localStorage，靠这里做兜底通道，
    # 从而「无需用户手动设置」也能跟上飞牛的日间/夜间模式。
    fnos_theme: str = ""
