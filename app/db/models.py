"""ORM 模型定义（SQLAlchemy 2.0 声明式）

三方言（sqlite / mysql / postgresql）共用同一套模型：
- String 长度仅 mysql/postgresql 生效，SQLite 忽略长度
- 金额用 Float(53)：MySQL 渲染为 DOUBLE，PostgreSQL 为 double precision，SQLite 为 REAL
- Text 用于不限长字段（Markdown 报告正文 / JSON 摘要），三方言均映射为 TEXT
- 表、唯一约束与索引由 Base.metadata.create_all 按方言幂等生成
"""

from sqlalchemy import Float, Integer, String, Text, UniqueConstraint, false, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# 标签列宽（逗号分隔存储）；写入截断（bill_service.normalize_tags）与列定义共用同一常量
TAGS_MAX_LENGTH = 255

# 默认账本 id（T-7.1 账本维度）：默认账本始终是首个创建的账本，id 与列级
# server_default 保持一致，保证「不传 ledger_id 的旧调用」落在同一本账上。
# 升级路径见 migrations._v8_add_ledger_dimension（老库加列时按实际 id 回填）。
DEFAULT_LEDGER_ID = 1


class Bill(Base):
    """账单流水（user_id 标识归属的飞牛账号，空串 = 本地/独立部署默认账号）"""

    __tablename__ = "bills"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", index=True
    )
    tx_time: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    account: Mapped[str] = mapped_column(
        String(16), nullable=False, default="wechat", index=True
    )
    tx_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="expense", index=True
    )
    merchant: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    amount: Mapped[float] = mapped_column(Float(53), nullable=False, default=0)
    category: Mapped[str] = mapped_column(
        String(64), nullable=False, default="其他", index=True
    )
    # 全局唯一（不按账号区分）：微信/支付宝交易号本身全局唯一，跨账号重复概率可忽略
    tx_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    remark: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # 自定义标签（逗号分隔存储，如 "出差,报销"）；报销标记；回收站软删除标记
    # server_default：解析器经 insert_ignore_rows 核心插入时不含这些列，DDL 默认值兜底
    tags: Mapped[str] = mapped_column(
        String(TAGS_MAX_LENGTH), nullable=False, default="", server_default=""
    )
    reimbursed: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false()
    )
    deleted: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false()
    )
    # 账本维度（T-7.1）：server_default 让解析器经 insert_ignore_rows 的核心
    # 插入（不含本列）也落在默认账本，保证升级前后行为一致
    ledger_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_LEDGER_ID,
        server_default=text(str(DEFAULT_LEDGER_ID)),
        index=True,
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "tx_time": self.tx_time,
            "account": self.account,
            "tx_type": self.tx_type,
            "merchant": self.merchant,
            "amount": self.amount,
            "category": self.category,
            "tx_id": self.tx_id,
            "remark": self.remark,
            "tags": self.tags,
            "reimbursed": self.reimbursed,
            "deleted": self.deleted,
            "ledger_id": self.ledger_id,
        }


class Category(Base):
    """消费分类"""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)


class Ledger(Base):
    """账本（T-7.1 账本维度）：流水 / 预算 / 资产快照都挂在某个账本下

    - 默认账本（is_default=True）全局唯一且不可删除：升级前已存在的历史数据
      全部挂到它上面，因此「不传 ledger_id 的旧调用」行为与升级前完全等价
      （旧调用一律落在默认账本，见 migrations._v8_add_ledger_dimension）
    - owner_id：账本归属账号（空串 = 应用级共享）。T-7.1 阶段只由迁移创建
      默认账本（owner_id 为空串），家庭空间与成员账本待 T-7.2 接入
    - 名称全局唯一：账本名是用户在界面上识别账本的唯一凭据
    """

    __tablename__ = "ledgers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    owner_id: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", index=True
    )
    is_default: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false()
    )
    remark: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # epoch 秒（与 AIReport 等表一致），用于列表排序
    created_at: Mapped[float] = mapped_column(nullable=False, default=0)
    updated_at: Mapped[float] = mapped_column(nullable=False, default=0)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "owner_id": self.owner_id,
            "is_default": self.is_default,
            "remark": self.remark,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class AppMeta(Base):
    """应用元信息（键值对），当前用于记录 schema 版本"""

    __tablename__ = "app_meta"

    meta_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    meta_value: Mapped[str] = mapped_column(String(255), nullable=False)


class Budget(Base):
    """月度预算（user_id + ledger_id + month + category 唯一；category 空串表示整月总预算）

    ledger_id 为 T-7.1 账本维度：同一账号在不同账本下可各设一份预算。
    唯一约束升级见 migrations._v8_add_ledger_dimension（SQLite 需重建表）。
    """

    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "ledger_id", "month", "category", name="uq_budget_scope"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", index=True
    )
    ledger_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_LEDGER_ID,
        server_default=text(str(DEFAULT_LEDGER_ID)),
        index=True,
    )
    month: Mapped[str] = mapped_column(String(7), nullable=False, index=True)  # YYYY-MM
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    amount: Mapped[float] = mapped_column(Float(53), nullable=False, default=0)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "ledger_id": self.ledger_id,
            "month": self.month,
            "category": self.category,
            "amount": self.amount,
        }


class AssetSnapshot(Base):
    """资产快照：某日记录各账户的资产/负债金额，用于净资产趋势追踪"""

    __tablename__ = "asset_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", index=True
    )
    ledger_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_LEDGER_ID,
        server_default=text(str(DEFAULT_LEDGER_ID)),
        index=True,
    )
    snap_date: Mapped[str] = mapped_column(
        String(10), nullable=False, index=True
    )  # YYYY-MM-DD
    name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    asset_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="asset", index=True
    )  # asset=资产 / liability=负债
    amount: Mapped[float] = mapped_column(Float(53), nullable=False, default=0)
    remark: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "ledger_id": self.ledger_id,
            "snap_date": self.snap_date,
            "name": self.name,
            "asset_type": self.asset_type,
            "amount": self.amount,
            "remark": self.remark,
        }


class ScheduledTask(Base):
    """定时任务定义（进程内调度器的任务注册表）

    时间字段统一存 epoch 秒（Float），便于跨方言比较与计算：
    - next_run_at：下次应执行时间；NULL 表示停用/等待手动触发
    - running_at：软锁（开始执行的时间戳）；NULL = 空闲。
      超过 LOCK_TIMEOUT 仍未释放视为死锁，可被重新认领
    - locked_by / locked_until：多实例部署的升级路径预留字段，
      单进程调度不使用（见 docs/devlog 开发计划 T-5.1 已知边界）
    """

    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    interval_minutes: Mapped[int] = mapped_column(nullable=False, default=30)
    next_run_at: Mapped[float | None] = mapped_column(nullable=True)
    running_at: Mapped[float | None] = mapped_column(nullable=True)
    locked_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    locked_until: Mapped[float | None] = mapped_column(nullable=True)
    failure_count: Mapped[int] = mapped_column(nullable=False, default=0)
    last_status: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    last_message: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    updated_at: Mapped[float] = mapped_column(nullable=False, default=0)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "task_key": self.task_key,
            "name": self.name,
            "enabled": self.enabled,
            "interval_minutes": self.interval_minutes,
            "next_run_at": self.next_run_at,
            "running_at": self.running_at,
            "failure_count": self.failure_count,
            "last_status": self.last_status,
            "last_message": self.last_message,
            "updated_at": self.updated_at,
        }


class TaskRun(Base):
    """定时任务运行历史：每次执行的起止、结果、错误摘要与影响条数"""

    __tablename__ = "task_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[float] = mapped_column(nullable=False)
    finished_at: Mapped[float] = mapped_column(nullable=False, default=0)
    ok: Mapped[bool] = mapped_column(nullable=False, default=True)
    error: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    affected: Mapped[int] = mapped_column(nullable=False, default=0)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "task_key": self.task_key,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "ok": self.ok,
            "error": self.error,
            "affected": self.affected,
        }


class ImportedFile(Base):
    """已处理导入文件登记（目录监听判重依据）

    以「路径 + 内容指纹」判重，不能只靠文件名：用户用同名文件重新导出覆盖很常见。
    path 经 sha256 摘要后存 path_key 做唯一键（路径长度不受限，且跨方言索引安全）；
    status = ok / failed / unknown（无法识别来源）。同内容失败文件不重试，
    仅当内容变化（指纹不同）时重新处理，避免失败文件被无限重试。
    """

    __tablename__ = "imported_files"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    path_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    size: Mapped[int] = mapped_column(nullable=False, default=0)
    mtime: Mapped[float] = mapped_column(nullable=False, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    message: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    inserted: Mapped[int] = mapped_column(nullable=False, default=0)
    updated_at: Mapped[float] = mapped_column(nullable=False, default=0)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "path": self.path,
            "file_name": self.file_name,
            "user_id": self.user_id,
            "size": self.size,
            "mtime": self.mtime,
            "content_hash": self.content_hash,
            "status": self.status,
            "message": self.message,
            "inserted": self.inserted,
            "updated_at": self.updated_at,
        }


class AIReport(Base):
    """AI 报告归档：按 (user_id, period_type, period_value) 唯一约束做 upsert

    period_type ∈ {month, quarter, half, year}，period_value 形如 2026-09 / 2026-Q1 /
    2026-H1 / 2026（见 app.utils.period）。stats_summary 存 json.dumps 后的统计上下文，
    供后续「口径可追溯」展开查看（T-6.5 范围，本版只落库备查）。
    重新归档同周期会覆盖旧版本（upsert），符合「该周期的最新快照」语义。
    """

    __tablename__ = "ai_reports"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "period_type", "period_value", name="uq_ai_report_scope"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", index=True
    )
    period_type: Mapped[str] = mapped_column(String(8), nullable=False, default="month")
    period_value: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # Markdown 报告正文：长度不限，避免长报告被截断
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 统计上下文 JSON：生成时引用的全部汇总数字，供口径溯源
    stats_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # epoch 秒（与 ScheduledTask 时间字段一致），用于列表排序与「最近归档」展示
    created_at: Mapped[float] = mapped_column(nullable=False, default=0)
    updated_at: Mapped[float] = mapped_column(nullable=False, default=0)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "period_type": self.period_type,
            "period_value": self.period_value,
            "title": self.title,
            "content": self.content,
            "stats_summary": self.stats_summary,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def as_list_dict(self) -> dict:
        """列表项：不含 content / stats_summary，避免列表接口传输 Markdown"""
        return {
            "id": self.id,
            "period_type": self.period_type,
            "period_value": self.period_value,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class Notification(Base):
    """应用内通知（T-5.4 通知中心，方案 B：应用内 + 自配出站 Webhook）

    - user_id：接收账号；空串 = 应用级广播（任务失败 / 自动导入完成等全局
      事件），对网关下所有登录用户可见；预算/报告等账号事件写具体账号
    - event_key：事件去重键（唯一约束）。同一事件（如同月同分类的超支提醒、
      并发重放的导入完成）只入库/推送一次；重复写入按冲突忽略
    - push_status / push_error：出站 Webhook 的投递结果（失败不阻塞主流程，
      原因落库供界面/日志追溯）
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", index=True
    )
    event_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", index=True
    )
    event_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    content: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    push_status: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    push_error: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[float] = mapped_column(nullable=False, default=0)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "event_type": self.event_type,
            "event_key": self.event_key,
            "title": self.title,
            "content": self.content,
            "push_status": self.push_status,
            "push_error": self.push_error,
            "created_at": self.created_at,
        }


class NotificationRead(Base):
    """通知已读水位线：每账号一行，记录已读到的最大通知 id

    不存逐条已读明细：通知是持续追加的流，账号未读数 = 「id 大于水位线且
    对该账号可见」的行数，一次比较即可完成角标计算，且无需随通知清理联动。
    """

    __tablename__ = "notification_reads"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_read_id: Mapped[int] = mapped_column(nullable=False, default=0)
    updated_at: Mapped[float] = mapped_column(nullable=False, default=0)


class LearnedRule(Base):
    """分类自学习规则（T-6.3）：商户关键词 → 分类，由用户手动纠正证据积累而来

    - (pattern, category) 唯一；同一 pattern 可有多行指向不同分类（用户改来改去
      的冲突场景），生效规则取 hits 最高者；匹配时再按 pattern 长度优先
      （更具体的商户名优先于宽泛词）
    - hits：同一纠正方向的累计次数；hits 达到 CONFIRM_THRESHOLD（2 次）才参与
      匹配——避免一次误改就污染全库（开发计划 T-6.3 触发方式）
    - enabled：手动停用开关（设置页规则列表可编辑/停用），停用后不参与匹配
    - 全局共享（无 user_id 维度）：分类本身全局共用，与内置关键词/AI 通道同口径
    """

    __tablename__ = "learned_rules"
    __table_args__ = (
        UniqueConstraint(
            "pattern", "category", name="uq_learned_rule_pattern_category"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pattern: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    hits: Mapped[int] = mapped_column(nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    # epoch 秒（与 AIReport 等表一致），用于规则列表排序
    created_at: Mapped[float] = mapped_column(nullable=False, default=0)
    updated_at: Mapped[float] = mapped_column(nullable=False, default=0)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "pattern": self.pattern,
            "category": self.category,
            "hits": self.hits,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
