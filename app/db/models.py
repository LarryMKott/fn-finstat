"""ORM 模型定义（SQLAlchemy 2.0 声明式）

三方言（sqlite / mysql / postgresql）共用同一套模型：
- String 长度仅 mysql/postgresql 生效，SQLite 忽略长度
- 金额用 Float(53)：MySQL 渲染为 DOUBLE，PostgreSQL 为 double precision，SQLite 为 REAL
- 表、唯一约束与索引由 Base.metadata.create_all 按方言幂等生成
"""

from sqlalchemy import Float, String
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
