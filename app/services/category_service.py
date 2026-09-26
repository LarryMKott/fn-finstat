"""消费分类业务逻辑

按领域划分的服务类（OOP 风格）：
- CategoryService 承载全部业务规则（名称校验、默认分类保护、并发兜底）
- 依赖通过构造函数注入（DAO 单例），便于测试替换
- 保持模块级同名函数作为对 API 路由层的薄封装（保证既有调用方零改动）

层级（v1.1）：两级为限——分类可有 parent_id 指向顶层分类，子分类不可再建子。
子类不参与自动归类（bills.category 仍存流水实际所在分类，是否迁入子类由
用户显式触发），预算/报表继续按名字平铺统计，零层级 rollup 破坏面。
"""

from app.config import DEFAULT_CATEGORY
from app.core.errors import ErrorCode, NotFoundError, ValidationError
from app.db.dao.bill_dao import BillDAO
from app.db.dao.category_dao import CategoryDAO
from app.schemas.category import CATEGORY_NAME_MAX_LENGTH
from app.utils.amount import round2


class CategoryService:
    """分类领域服务

    职责：
    - 名称规则校验（非空、长度、唯一性）
    - 默认分类保护（不可重命名 / 不可删除）
    - 层级规则（两级为限、父分类存在性、有子分类不可删）
    - 流水数按账号统计（与 get_category 的 user_id 联动）
    - 唯一约束兜底：DAO 层抛 IntegrityError → ValidationError（"分类已存在"）

    协作：
    - CategoryDAO：CRUD + 唯一约束兜底
    - BillDAO：按当前账号统计分类下的流水数量
    """

    def __init__(
        self,
        category_dao: type[CategoryDAO] = CategoryDAO,
        bill_dao: type[BillDAO] = BillDAO,
    ) -> None:
        # 接受类而非实例：保留既有静态方法调用约定（DAO 是纯静态类，零状态）
        # 测试时可注入假 DAO
        self._category_dao = category_dao
        self._bill_dao = bill_dao

    @staticmethod
    def _with_children(cats: list[dict]) -> list[dict]:
        """给扁平分类列表附 children id 列表（一次遍历，顺序不变）"""
        children_map: dict = {}
        for c in cats:
            children_map.setdefault(c["parent_id"], []).append(c["id"])
        return [{**c, "children": children_map.get(c["id"], [])} for c in cats]

    def list_all(self) -> list[dict]:
        """全部分类列表（分类全局共享，不区分账号），children 为子分类 id"""
        return self._with_children(self._category_dao.list_all())

    def get(self, category_id: int, user_id: str | None = None) -> dict:
        """分类详情；bill_count 按当前账号统计（user_id 为 None 时统计全部账号）"""
        cat = self._category_dao.get_by_id(category_id)
        if cat is None:
            raise NotFoundError("分类不存在")
        children = [
            c["id"]
            for c in self._category_dao.list_all()
            if c["parent_id"] == category_id
        ]
        return {
            **cat,
            "children": children,
            "bill_count": self._bill_dao.count_by_category(cat["name"], user_id),
        }

    def tree(self, user_id: str | None = None) -> list[dict]:
        """分类树（仅顶层节点，子分类嵌套在 children），每节点 rollup 含子孙

        rollup 口径与流水列表一致：排除回收站；expense_total 只累计支出类型。
        统计按分类名从 BillDAO 一次聚合取回（bills 按名字关联分类），父子
        归并在内存完成后序累加。
        """
        cats = self._category_dao.list_all()
        stats = self._bill_dao.category_stats(user_id)
        nodes: dict[int, dict] = {}
        roots: list[dict] = []
        for c in cats:
            st = stats.get(c["name"]) or {"count": 0, "expense": 0.0}
            nodes[c["id"]] = {
                "id": c["id"],
                "name": c["name"],
                "parent_id": c["parent_id"],
                "source": c["source"],
                "bill_count": st["count"],
                "expense_total": st["expense"],
                "children": [],
            }
        for c in cats:
            parent = nodes.get(c["parent_id"]) if c["parent_id"] else None
            if parent is not None:
                parent["children"].append(nodes[c["id"]])
            else:
                roots.append(nodes[c["id"]])

        def _rollup(node: dict) -> None:
            for child in node["children"]:
                _rollup(child)
                node["bill_count"] += child["bill_count"]
                node["expense_total"] = round2(
                    node["expense_total"] + child["expense_total"]
                )

        for root in roots:
            _rollup(root)
        return roots

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

    def create(
        self, name: str, parent_id: int | None = None, source: str = "manual"
    ) -> dict:
        """新增分类：名称非空、长度、重复校验，唯一约束兜底并发

        parent_id 非空时校验父分类存在且自身为顶层（两级为限）。
        """
        name = self._validate_name(name)
        if parent_id is not None:
            parent = self._category_dao.get_by_id(parent_id)
            if parent is None:
                raise NotFoundError("父分类不存在")
            if parent["parent_id"] is not None:
                raise ValidationError(
                    "子分类下不可再建子分类（层级以两级为限）",
                    code=ErrorCode.CATEGORY_INVALID,
                )
        if self._category_dao.get_by_name(name):
            raise ValidationError("分类已存在", code=ErrorCode.CATEGORY_INVALID)
        category_id = self._category_dao.create(
            name, parent_id=parent_id, source=source
        )
        if category_id is None:
            # DAO 内部唯一约束兜底：并发预检查漏过时由数据库兜住，转业务异常
            raise ValidationError("分类已存在", code=ErrorCode.CATEGORY_INVALID)
        return {
            "id": category_id,
            "name": name,
            "parent_id": parent_id,
            "source": source,
            "children": [],
        }

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
        """删除分类，其下流水归入「其他」

        有子分类时拒绝（防孤儿层级），要求先删除或移走子分类；分类的关键词
        由 DAO 同事务级联清理。
        """
        cat = self._category_dao.get_by_id(category_id)
        if cat is None:
            raise NotFoundError("分类不存在")
        self._check_default_protection(cat, "", "delete")
        if self._category_dao.has_children(category_id):
            raise ValidationError(
                f"分类「{cat['name']}」下存在子分类，请先删除或移走子分类",
                code=ErrorCode.CATEGORY_INVALID,
            )
        moved = self._category_dao.delete(category_id, fallback=DEFAULT_CATEGORY)
        return {"id": category_id, "name": cat["name"], "moved_bills": moved}


# ---- 模块级薄封装：保留既有 API 路由调用方式（保证零改动） ----
# 单例：服务无内部状态，模块级单例等价于每次 new，但避免重复构造
_service = CategoryService()


def list_categories() -> list[dict]:
    return _service.list_all()


def get_category(category_id: int, user_id: str | None = None) -> dict:
    return _service.get(category_id, user_id)


def category_tree(user_id: str | None = None) -> list[dict]:
    return _service.tree(user_id)


def create_category(
    name: str, parent_id: int | None = None, source: str = "manual"
) -> dict:
    return _service.create(name, parent_id=parent_id, source=source)


def update_category(category_id: int, name: str) -> dict:
    return _service.rename(category_id, name)


def delete_category(category_id: int) -> dict:
    return _service.delete(category_id)
