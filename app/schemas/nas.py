"""NAS 目录导入模型"""

from pydantic import BaseModel, Field


class NasConfigUpdate(BaseModel):
    """保存账单目录：绝对路径（如 fnOS 的 /vol1/1000/bills）"""

    import_dir: str = Field(min_length=1)


class NasConfigOut(BaseModel):
    """账单目录配置；exists 为目录当前是否可访问"""

    import_dir: str = ""
    exists: bool = False
    supported_exts: list[str] = []


class NasEntry(BaseModel):
    """目录条目：is_dir=True 时为子目录；文件仅包含支持的账单后缀"""

    name: str
    path: str  # 相对账单目录的路径（进入子目录/导入时回传）
    is_dir: bool
    size: int = 0
    modified: float = 0  # 文件修改时间（epoch 秒）
    source: str = ""  # 识别出的来源 key（wechat/alipay/jd/unionpay），未识别为 unknown


class NasDirectory(BaseModel):
    """目录浏览结果：子目录与账单文件分开返回（各自按名称排序）"""

    root: str  # 账单目录的展示名（仅最后一级目录名），不回传完整绝对路径
    path: str  # 当前相对路径，根目录为空串
    parent: str  # 上一级相对路径，根目录为空串
    dirs: list[NasEntry]
    files: list[NasEntry]


class NasImportRequest(BaseModel):
    """导入账单目录中的文件：path 为浏览接口返回的相对路径"""

    path: str = Field(min_length=1)


class NasAuthorizationStatus(BaseModel):
    """当前用户账单目录授权状态（飞牛环境）

    字段语义对应 app.services.nas_authorization_service.UserAuthorizationStatus：
    - available: 当前进程是否在飞牛 fnOS 环境且具备 trim 网关能力
    - authorized: 当前用户是否授权过至少一个目录
    - folders: 用户已授权的目录路径列表（available=False 时为空）
    - reason: unavailable / partially-fulfilled 的原因文案，前端可原样展示
    - uid: 当前用户映射到的数字 uid（available=True 时有值，否则为 0）
    - shared_folders: 管理员在「系统设置 > 应用」里授权给本应用的共享目录
    - shared_reason: 共享目录查询失败的原因文案（成功时为空串）
    - is_admin: 当前用户是否管理员（决定前端是否给出共享授权入口）
    """

    available: bool
    authorized: bool
    folders: list[str] = []
    reason: str = ""
    uid: int = 0
    shared_folders: list[str] = []
    shared_reason: str = ""
    is_admin: bool = False


class NasAclCheckRequest(BaseModel):
    """对一组账单目录内路径做 ACL 检查（飞牛环境）"""

    paths: list[str] = Field(default_factory=list, max_length=200)
