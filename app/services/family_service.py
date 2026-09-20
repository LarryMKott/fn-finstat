"""家庭空间业务逻辑（T-7.2 家庭空间与合并视图）

家庭是多个飞牛账号的聚合容器：成员各自记账（数据仍按 user_id 隔离），
家庭页展示聚合值。核心边界（REQ-FAM-002/003）：
- 聚合 = 各成员实际之和：汇总按成员逐个查询后在 Python 合并，家庭规模小
  （N 次索引查询），换取逐项可核对的加总口径，且不改动 StatDAO 单账号签名
- 明细默认互不可见：allow_detail_view=False 时成员只能看到聚合值；开启后
  可读其他成员的流水列表（只读），该开关仅家庭管理员可改，默认关闭

权限模型：家庭管理员是家庭内部角色（创建人），与飞牛应用管理员无关；
家庭操作只影响本家庭，因此路由层用 CurrentUser，管理员校验在服务层收口
（不进 core/permissions 的应用级管理面策略表）。
"""

from typing import Optional

from app.core.constants import ROLE_ADMIN
from app.core.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.db.dao.bill_dao import BillDAO
from app.db.dao.family_dao import FamilyDAO
from app.db.dao.stat_dao import StatDAO
from app.schemas.family import FamilySettingsUpdate
from app.services import audit_service
from app.utils.amount import round2
from app.utils.period import month_range, valid_month

FAMILY_NAME_MAX = 64
NICKNAME_MAX = 64

_MEMBER_FIELDS = {"user_id", "role", "nickname", "joined_at"}


def _require_member(user_id: str) -> dict:
    """要求当前账号已加入家庭，返回其成员行"""
    member = FamilyDAO.member_of(user_id)
    if member is None:
        raise NotFoundError("你还没有加入任何家庭")
    return member


def _require_family_admin(user_id: str) -> dict:
    """要求当前账号是家庭管理员，返回其成员行"""
    member = _require_member(user_id)
    if member["role"] != ROLE_ADMIN:
        raise PermissionDeniedError("该操作仅限家庭管理员")
    return member


def _require_member_family(user_id: str) -> tuple[dict, dict]:
    """要求当前账号已加入家庭且家庭真实存在，返回（成员行, 家庭行）

    成员行指向已解散的家庭（并发解散/历史脏数据产生的孤儿成员行）时按
    「未加入家庭」处理并给 404——此前各读接口自行兜底，summary /
    member_bills 漏了会直接 500，且该账号从此 create/join 一律 409，
    界面显示未加入家庭却无法自救。
    """
    member = _require_member(user_id)
    family = FamilyDAO.get(member["family_id"])
    if family is None:
        raise NotFoundError("你所在的家庭不存在或已解散，请退出后重新加入或创建")
    return member, family


def _member_view(member: dict) -> dict:
    """对外成员视图：只透出展示字段"""
    return {k: member[k] for k in _MEMBER_FIELDS}


def my_family(user_id: str) -> Optional[dict]:
    """当前账号的家庭信息；未加入返回 None

    邀请码仅家庭管理员可见（成员无需持有，避免扩散面）。
    """
    member = FamilyDAO.member_of(user_id)
    if member is None:
        return None
    family = FamilyDAO.get(member["family_id"])
    if family is None:  # 数据异常兜底：成员行指向已解散的家庭，按无家庭处理
        return None
    info = {
        "id": family["id"],
        "name": family["name"],
        "allow_detail_view": family["allow_detail_view"],
        "created_at": family["created_at"],
        "members": [_member_view(m) for m in FamilyDAO.list_members(family["id"])],
        "my_role": member["role"],
        "invite_code": family["invite_code"] if member["role"] == ROLE_ADMIN else None,
    }
    return info


def create_family(name: str, user_id: str, nickname: str = "") -> dict:
    """创建家庭（创建人成为家庭管理员）；已在家庭中拒绝重复创建"""
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValidationError("家庭名称不能为空")
    if len(cleaned) > FAMILY_NAME_MAX:
        raise ValidationError(f"家庭名称不能超过 {FAMILY_NAME_MAX} 个字符")
    if FamilyDAO.member_of(user_id) is not None:
        raise ConflictError("你已加入一个家庭，不能重复创建")
    created = FamilyDAO.create(cleaned, user_id, (nickname or "")[:NICKNAME_MAX])
    audit_service.record(
        user_id, "family.create", "family", created["id"], "创建家庭「" + cleaned + "」"
    )
    return {
        **created,
        "my_role": ROLE_ADMIN,
    }


def join_family(code: str, user_id: str, nickname: str = "") -> dict:
    """凭邀请码加入家庭；码无效 / 已在家庭中分别 404 / 409"""
    family = FamilyDAO.get_by_invite_code(code or "")
    if family is None:
        raise NotFoundError("邀请码无效或已失效")
    if FamilyDAO.member_of(user_id) is not None:
        raise ConflictError("你已加入一个家庭，请先退出后再加入")
    FamilyDAO.join(family["id"], user_id, (nickname or "")[:NICKNAME_MAX])
    audit_service.record(
        user_id,
        "family.join",
        "family",
        family["id"],
        "加入家庭「" + family["name"] + "」",
    )
    return {"family_id": family["id"], "family_name": family["name"]}


def leave_family(user_id: str) -> None:
    """退出家庭：普通成员直接退出；管理员在还有成员时须先解散（或移除全部成员）

    退出与「最后一人自动解散（含预算清理）」在同一事务内完成（见
    FamilyDAO.leave_and_maybe_disband），不再有中途失败留下的 0 成员家庭。
    """
    member = _require_member(user_id)
    family_id = member["family_id"]
    if member["role"] == ROLE_ADMIN and FamilyDAO.count_members(family_id) > 1:
        raise ValidationError("家庭管理员不能直接退出，请先解散家庭")
    disbanded = FamilyDAO.leave_and_maybe_disband(family_id, user_id)
    if not disbanded:
        raise NotFoundError("你还没有加入任何家庭")
    if FamilyDAO.get(family_id) is None:
        audit_service.record(
            user_id, "family.disband", "family", family_id, "退出后家庭无成员，自动解散"
        )
    else:
        audit_service.record(user_id, "family.leave", "family", family_id, "退出家庭")


def remove_member(requester_id: str, target_user_id: str) -> None:
    """移除成员（家庭管理员）：管理员用退出，不能移除自己"""
    admin = _require_family_admin(requester_id)
    if target_user_id == requester_id:
        raise ValidationError("不能移除自己，请使用退出家庭")
    if not FamilyDAO.remove_member(admin["family_id"], target_user_id):
        raise NotFoundError("该成员不存在或已不在本家庭")
    audit_service.record(
        requester_id,
        "family.member_remove",
        "family",
        admin["family_id"],
        "移除成员 " + target_user_id,
    )


def disband_family(requester_id: str) -> None:
    """解散家庭（家庭管理员）：预算、成员与家庭在同一事务内清除，各成员数据不受影响"""
    admin = _require_family_admin(requester_id)
    family = FamilyDAO.get(admin["family_id"])
    if not FamilyDAO.disband_with_budgets(admin["family_id"]):
        raise NotFoundError("家庭不存在或已解散")
    audit_service.record(
        requester_id,
        "family.disband",
        "family",
        admin["family_id"],
        "解散家庭「" + ((family or {}).get("name") or "?") + "」",
    )


def update_settings(requester_id: str, payload: FamilySettingsUpdate) -> dict:
    """更新家庭设置（家庭管理员）：当前仅明细可见性开关"""
    admin = _require_family_admin(requester_id)
    updated = FamilyDAO.update_settings(
        admin["family_id"], {"allow_detail_view": payload.allow_detail_view}
    )
    audit_service.record(
        requester_id,
        "family.settings",
        "family",
        admin["family_id"],
        "家庭设置：成员明细可见 = " + ("开启" if payload.allow_detail_view else "关闭"),
    )
    return {"allow_detail_view": updated["allow_detail_view"]}


def regenerate_invite_code(requester_id: str) -> dict:
    """重新生成邀请码（家庭管理员）：旧码立即失效"""
    admin = _require_family_admin(requester_id)
    code = FamilyDAO.regenerate_invite_code(admin["family_id"])
    audit_service.record(
        requester_id,
        "family.invite_regenerate",
        "family",
        admin["family_id"],
        "重新生成邀请码",
    )
    return {"invite_code": code}


def summary(user_id: str, month: str) -> dict:
    """家庭月度汇总（家庭页聚合视图，成员与普通成员口径一致）

    totals = 各成员之和；members 给出逐成员收支与流水条数（可核对加总）；
    categories 为全员支出分类占比（按分类名合并后降序）。
    """
    member, family = _require_member_family(user_id)
    if not valid_month(month):
        raise ValidationError("无效的月份格式，应为 YYYY-MM")
    start, end = month_range(month)

    members = []
    totals = {"income": 0.0, "expense": 0.0, "net": 0.0}
    category_totals: dict[str, float] = {}
    for m in FamilyDAO.list_members(family["id"]):
        uid = m["user_id"]
        stat = StatDAO.summary(uid, start, end)
        income, expense = round2(stat["income"]), round2(stat["expense"])
        bill_count = BillDAO.count_in_range(uid, start, end)
        members.append(
            {
                **_member_view(m),
                "income": income,
                "expense": expense,
                "net": round(income - expense, 2),
                "bill_count": bill_count,
            }
        )
        totals["income"] += income
        totals["expense"] += expense
        for row in StatDAO.category_pie(uid, start=start, end=end):
            category_totals[row["name"]] = category_totals.get(
                row["name"], 0.0
            ) + float(row["value"])

    totals["income"] = round2(totals["income"])
    totals["expense"] = round2(totals["expense"])
    totals["net"] = round(totals["income"] - totals["expense"], 2)
    categories = [
        {"name": name, "value": round2(value)}
        for name, value in sorted(
            category_totals.items(), key=lambda kv: kv[1], reverse=True
        )
    ]
    return {
        "month": month,
        "family": {
            "id": family["id"],
            "name": family["name"],
            "allow_detail_view": family["allow_detail_view"],
        },
        "totals": totals,
        "members": members,
        "categories": categories,
    }


def member_bills(
    requester_id: str,
    target_user_id: str,
    page: int = 1,
    page_size: int = 20,
    month: Optional[str] = None,
) -> dict:
    """查看成员流水（只读）：须本家庭成员 + 家庭开启明细可见

    开关关闭时一律 403（即使是管理员）——隐私边界由家庭设置决定，不因角色放行。
    """
    requester, family = _require_member_family(requester_id)
    target = FamilyDAO.member_of(target_user_id)
    if target is None or target["family_id"] != requester["family_id"]:
        raise NotFoundError("该成员不存在或已不在本家庭")
    if not family["allow_detail_view"]:
        raise PermissionDeniedError("家庭未开放成员明细查看")
    start = end = None
    if month is not None:
        if not valid_month(month):
            raise ValidationError("无效的月份格式，应为 YYYY-MM")
        start, end = month_range(month)
    total, rows = BillDAO.list_bills(
        target_user_id,
        page=page,
        page_size=page_size,
        start=start,
        end=end,
    )
    return {"total": total, "rows": rows}
