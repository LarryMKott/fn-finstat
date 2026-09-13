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

    root: str
    path: str  # 当前相对路径，根目录为空串
    parent: str  # 上一级相对路径，根目录为空串
    dirs: list[NasEntry]
    files: list[NasEntry]


class NasImportRequest(BaseModel):
    """导入账单目录中的文件：path 为浏览接口返回的相对路径"""

    path: str = Field(min_length=1)
