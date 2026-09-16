"""统一业务错误体系：错误码常量 + 业务异常族

分层约定：服务层 / DAO 层只抛 BizError 子类表达业务失败，路由层不写
try/except 翻译，由全局异常处理器（app/core/handlers.py）统一把异常转换为
统一响应体 {"code", "msg", "data"}（HTTP 状态码保留语义）。

错误码分段（code 取值）：
    0        成功
    100xx    通用（参数 / 权限 / 不存在 / 冲突 / 上传限制 / 未认证）
    400xx    账单流水
    410xx    消费分类
    420xx    账单导入 / 解析
    430xx    NAS 目录导入
    440xx    预算
    450xx    资产快照
    470xx    设置 / 数据库
    480xx    智能分类（AI）
    50000    服务器内部错误
"""

from __future__ import annotations


class ErrorCode:
    """统一错误码（统一响应体 code 字段的全部合法取值）"""

    OK = 0

    # ---- 通用 ----
    BAD_REQUEST = 10001  # 参数校验失败（含请求体 422 校验）
    FORBIDDEN = 10002  # 权限不足（如非管理员执行管理操作）
    NOT_FOUND = 10003  # 资源不存在
    CONFLICT = 10004  # 数据冲突（唯一约束等）
    UPLOAD_TOO_LARGE = 10005  # 上传/导入文件超限
    UNAUTHORIZED = 10006  # 未认证（fnOS 网关模式缺失网关身份头）
    INTERNAL_ERROR = 50000  # 未预期异常

    # ---- 账单流水 ----
    BILL_INVALID = 40001  # 收支类型/账户/金额/排序等不合法
    BILL_TX_ID_DUP = 40002  # 交易单号重复

    # ---- 消费分类 ----
    CATEGORY_INVALID = 41001  # 名称非法 / 重复 / 默认分类受保护

    # ---- 账单导入 ----
    IMPORT_PARSE_FAILED = 42001  # 账单解析失败
    IMPORT_SOURCE_UNKNOWN = 42002  # 无法识别账单来源

    # ---- NAS 目录导入 ----
    NAS_DIR_INVALID = 43001  # 目录未配置 / 越界 / 不可访问

    # ---- 预算 ----
    BUDGET_INVALID = 44001  # 月份 / 金额 / 分类不合法
    BUDGET_NOT_FOUND = 44002

    # ---- 资产快照 ----
    ASSET_INVALID = 45001
    ASSET_NOT_FOUND = 45002

    # ---- 设置 / 数据库 ----
    DB_CONFIG_INVALID = 47001  # 目标库配置 / 连接失败
    DB_MIGRATE_FAILED = 47002  # 迁移失败
    LOG_NOT_FOUND = 47003  # 运行日志不存在

    # ---- 智能分类（AI）----
    AI_NOT_CONFIGURED = 48001  # 未配置 API Key
    AI_CALL_FAILED = 48002  # 调用失败 / 任务重入


class BizError(Exception):
    """业务异常基类：携带统一错误码与对应 HTTP 状态码

    message 面向用户展示（前端 toast 直接使用），不泄露堆栈与驱动细节。
    """

    default_code: int = ErrorCode.BAD_REQUEST
    default_status: int = 400

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = self.default_code if code is None else code
        self.http_status = self.default_status if http_status is None else http_status


class ValidationError(BizError):
    """请求参数 / 业务规则不合法（HTTP 400）"""

    default_code = ErrorCode.BAD_REQUEST


class PermissionDeniedError(BizError):
    """权限不足（HTTP 403）"""

    default_code = ErrorCode.FORBIDDEN
    default_status = 403


class UnauthorizedError(BizError):
    """未认证（HTTP 401）：fnOS 网关模式下缺失网关身份头，身份未知而非权限不足"""

    default_code = ErrorCode.UNAUTHORIZED
    default_status = 401


class NotFoundError(BizError):
    """资源不存在（HTTP 404）"""

    default_code = ErrorCode.NOT_FOUND
    default_status = 404


class ConflictError(BizError):
    """数据冲突（唯一约束等；HTTP 400，与既有契约保持一致不使用 409）"""

    default_code = ErrorCode.CONFLICT


class UploadTooLargeError(BizError):
    """上传 / 导入文件超过大小限制（HTTP 400）"""

    default_code = ErrorCode.UPLOAD_TOO_LARGE


class ImportParseError(BizError):
    """账单解析失败（HTTP 400；文件内容问题，非程序错误）"""

    default_code = ErrorCode.IMPORT_PARSE_FAILED


class ConfigError(BizError):
    """配置 / 环境错误：驱动缺失、目标库连不上、迁移失败等（HTTP 400，message 含操作指引）"""

    default_code = ErrorCode.DB_CONFIG_INVALID


class EnvironmentError_(BizError):
    """运行环境问题（驱动缺失 / 连接错误等，HTTP 500）

    与「文件内容问题」（ImportParseError）区分开，避免把环境故障伪装成用户输入错误。
    """

    default_code = ErrorCode.INTERNAL_ERROR
    default_status = 500
