"""ORM 模型定义（SQLAlchemy 2.0 声明式）

三方言（sqlite / mysql / postgresql）共用同一套模型：
- String 长度仅 mysql/postgresql 生效，SQLite 忽略长度
- 金额用 Float(53)：MySQL 渲染为 DOUBLE，PostgreSQL 为 double precision，SQLite 为 REAL
- 表、唯一约束与索引由 Base.metadata.create_all 按方言幂等生成
"""

from sqlalchemy import Float, String, UniqueConstraint, false
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


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
        String(255), nullable=False, default="", server_default=""
    )
    reimbursed: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false()
    )
    deleted: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false()
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
        }


class Category(Base):
    """消费分类"""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)


class AppMeta(Base):
    """应用元信息（键值对），当前用于记录 schema 版本"""

    __tablename__ = "app_meta"

    meta_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    meta_value: Mapped[str] = mapped_column(String(255), nullable=False)


class Budget(Base):
    """月度预算（user_id + month + category 唯一；category 空串表示整月总预算）"""

    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("user_id", "month", "category", name="uq_budget_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", index=True
    )
    month: Mapped[str] = mapped_column(String(7), nullable=False, index=True)  # YYYY-MM
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    amount: Mapped[float] = mapped_column(Float(53), nullable=False, default=0)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
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
            "snap_date": self.snap_date,
            "name": self.name,
            "asset_type": self.asset_type,
            "amount": self.amount,
            "remark": self.remark,
        }
