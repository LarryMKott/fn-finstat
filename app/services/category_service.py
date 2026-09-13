"""消费分类业务逻辑

按领域划分的服务类（OOP 风格）：
- CategoryService 承载全部业务规则（名称校验、默认分类保护、并发兜底）
- 依赖通过构造函数注入（DAO 单例），便于测试替换
- 保持模块级同名函数作为对 API 路由层的薄封装（保证既有调用方零改动）
"""

from app.config import DEFAULT_CATEGORY
from app.core.errors import ErrorCode, NotFoundError, ValidationError
from app.db.dao.bill_dao import BillDAO
from app.db.dao.category_dao import CategoryDAO
from app.schemas.category import CATEGORY_NAME_MAX_LENGTH


class CategoryService:
    """分类领域服务

    职责：
    - 名称规则校验（非空、长度、唯一性）
    - 默认分类保护（不可重命名 / 不可删除）
    - 流水数按账号统计（与 get_category 的 user_id 联动）
    - 唯一约束兜底：DAO 层抛 IntegrityError → ValidationError（"分类已存在"）

    协作：
    - CategoryDAO：CRUD + 唯一约束兜底
    - BillDAO：按当前账号统计分类下的流水数量
    """

    def __init__(self, category_dao: type[CategoryDAO] = CategoryDAO, bill_dao: type[BillDAO] = BillDAO) -> None:
        # 接受类而非实例：保留既有静态方法调用约定（DAO 是纯静态类，零状态）
        # 测试时可注入假 DAO
        self._category_dao = category_dao
        self._bill_dao = bill_dao

    def list_all(self) -> list[dict]:
        """全部分类列表（分类全局共享，不区分账号）"""
        return self._category_dao.list_all()

    def get(self, category_id: int, user_id: str | None = None) -> dict:
        """分类详情；bill_count 按当前账号统计（user_id 为 None 时统计全部账号）"""
        cat = self._category_dao.get_by_id(category_id)
        if cat is None:
            raise NotFoundError("分类不存在")
        return {**cat, "bill_count": self._bill_dao.count_by_category(cat["name"], user_id)}

    def _validate_name(self, name: str) -> str:
        """分类名非空与长度校验（与 schema 的 CATEGORY_NAME_MAX_LENGTH 同源）"""
        name = name.strip()
        if not name:
            raise ValidationError("分类名称不能为空", code=ErrorCode.CATEGORY_INVALID)
        if len(name) > CATEGORY_NAME_MAX_LENGTH:
            raise ValidationError(
                f"分类名称不能超过 {CATEGORY_NAME_MAX_LENGTH} 个字符",
                code=ErrorCode.CATEGORY_INVALID,
            )
        return name

    def _check_default_protection(self, cat: dict, new_name: str, action: str) -> None:
        """默认分类保护：「其他」不可重命名/不可删除（action 决定错误信息）"""
        if cat["name"] != DEFAULT_CATEGORY:
            return
        if action == "rename" and new_name != DEFAULT_CATEGORY:
            raise ValidationError(
                f"默认分类「{DEFAULT_CATEGORY}」不可重命名",
                code=ErrorCode.CATEGORY_INVALID,
            )
        if action == "delete":
            raise ValidationError(
                f"默认分类「{DEFAULT_CATEGORY}」不可删除",
                code=ErrorCode.CATEGORY_INVALID,
            )

    def create(self, name: str) -> dict:
        """新增分类：名称非空、长度、重复校验，唯一约束兜底并发"""
        name = self._validate_name(name)
        if self._category_dao.get_by_name(name):
            raise ValidationError("分类已存在", code=ErrorCode.CATEGORY_INVALID)
        category_id = self._category_dao.create(name)
        if category_id is None:
            # DAO 内部唯一约束兜底：并发预检查漏过时由数据库兜住，转业务异常
            raise ValidationError("分类已存在", code=ErrorCode.CATEGORY_INVALID)
        return {"id": category_id, "name": name}

    def rename(self, category_id: int, name: str) -> dict:
        """重命名分类，并同步更新该分类下的所有流水"""
        name = self._validate_name(name)
        cat = self._category_dao.get_by_id(category_id)
        if cat is None:
            raise NotFoundError("分类不存在")
        self._check_default_protection(cat, name, "rename")
        if cat["name"] == name:
            return {"id": category_id, "name": name, "renamed_bills": 0}
        dup = self._category_dao.get_by_name(name)
        if dup and dup["id"] != category_id:
            raise ValidationError("分类已存在", code=ErrorCode.CATEGORY_INVALID)
        # DAO 层以唯一约束兜底并发重名（转 ValidationError 语义的 ConflictError）
        renamed = self._category_dao.rename(category_id, name)
        return {"id": category_id, "name": name, "renamed_bills": renamed}

    def delete(self, category_id: int) -> dict:
        """删除分类，其下流水归入「其他」"""
        cat = self._category_dao.get_by_id(category_id)
        if cat is None:
            raise NotFoundError("分类不存在")
        self._check_default_protection(cat, "", "delete")
        moved = self._category_dao.delete(category_id, fallback=DEFAULT_CATEGORY)
        return {"id": category_id, "name": cat["name"], "moved_bills": moved}


# ---- 模块级薄封装：保留既有 API 路由调用方式（保证零改动） ----
# 单例：服务无内部状态，模块级单例等价于每次 new，但避免重复构造
_service = CategoryService()


def list_categories() -> list[dict]:
    return _service.list_all()


def get_category(category_id: int, user_id: str | None = None) -> dict:
    return _service.get(category_id, user_id)


def create_category(name: str) -> dict:
    return _service.create(name)


def update_category(category_id: int, name: str) -> dict:
    return _service.rename(category_id, name)


def delete_category(category_id: int) -> dict:
    return _service.delete(category_id)
