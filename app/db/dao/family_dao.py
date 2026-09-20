"""家庭空间数据访问层（T-7.2 家庭空间与合并视图）

数据操作要点：
- 一个飞牛账号至多加入一个家庭（family_members.user_id 全局唯一），重复加入由
  唯一约束兜底转业务异常
- 邀请码全局唯一（families.invite_code），重新生成 = 覆盖旧码，旧码自然失效
- 家庭聚合统计在服务层按成员逐个查询后合并（家庭规模小，N 次小索引查询换取
  「汇总 = 成员之和」的逐项可核对性，且不动 StatDAO 的单账号签名）
"""

import secrets
import time
from typing import Optional

from sqlalchemy import case, delete, func, select

from app.core.constants import FAMILY_ROLES, ROLE_ADMIN, ROLE_MEMBER
from app.core.errors import NotFoundError
from app.db.base import get_db, translate_unique_violation
from app.db.models import Family, FamilyMember

__all__ = ["FamilyDAO", "FAMILY_ROLES"]

# 角色取值收敛到 app.core.constants，此处保留再导出（既有导入方不受影响）

# 邀请码字母表：去掉易混淆字符（0/O、1/I/L），8 位约 2^40 种组合，暴力猜解不现实
_INVITE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def _new_invite_code() -> str:
    return "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(8))


class FamilyDAO:
    @staticmethod
    def get(family_id: int) -> Optional[dict]:
        with get_db() as session:
            family = session.get(Family, family_id)
            return family.as_dict() if family is not None else None

    @staticmethod
    def get_by_invite_code(code: str) -> Optional[dict]:
        with get_db() as session:
            family = session.scalar(
                select(Family).where(Family.invite_code == code.strip().upper())
            )
            return family.as_dict() if family is not None else None

    @staticmethod
    def member_of(user_id: str) -> Optional[dict]:
        """当前账号的家庭成员行（含 family_id / role），未加入任何家庭返回 None"""
        with get_db() as session:
            member = session.scalar(
                select(FamilyMember).where(FamilyMember.user_id == user_id)
            )
            return member.as_dict() if member is not None else None

    @staticmethod
    def list_members(family_id: int) -> list[dict]:
        """成员列表（管理员在前，其余按加入时间升序）

        role 字典序 member > admin，直接 desc 会把普通成员排前面，
        因此用 case 显式把 admin 映射为 0 参与排序（三方言一致）。
        """
        admin_first = case((FamilyMember.role == ROLE_ADMIN, 0), else_=1)
        with get_db() as session:
            rows = session.scalars(
                select(FamilyMember)
                .where(FamilyMember.family_id == family_id)
                .order_by(admin_first, FamilyMember.joined_at)
            )
            return [m.as_dict() for m in rows]

    @staticmethod
    def count_members(family_id: int) -> int:
        with get_db() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(FamilyMember)
                    .where(FamilyMember.family_id == family_id)
                )
            )

    @staticmethod
    def create(name: str, creator_id: str, nickname: str = "") -> dict:
        """建家庭并把创建人写为管理员成员（同事务，避免有家庭无成员的中间态）"""
        now = time.time()
        with get_db() as session, translate_unique_violation("操作冲突，请重试"):
            family = Family(
                name=name,
                invite_code=_new_invite_code(),
                allow_detail_view=False,
                created_by=creator_id,
                created_at=now,
                updated_at=now,
            )
            session.add(family)
            session.flush()
            session.add(
                FamilyMember(
                    family_id=family.id,
                    user_id=creator_id,
                    role=ROLE_ADMIN,
                    nickname=nickname,
                    joined_at=now,
                )
            )
            session.flush()
            return family.as_dict()

    @staticmethod
    def join(family_id: int, user_id: str, nickname: str = "") -> dict:
        """凭邀请码加入家庭；已在任何家庭时由 user_id 唯一约束兜底转冲突

        家庭存在性在同一事务内复核：服务层查码与写入之间家庭可能被并发
        解散，不加这道检查会产生指向不存在家庭的孤儿成员行（成员从此
        create/join 全 409，页面却显示「未加入家庭」）。
        """
        with get_db() as session, translate_unique_violation("你已加入一个家庭"):
            family = session.get(Family, family_id)
            if family is None:
                raise NotFoundError("该家庭不存在或已解散")
            member = FamilyMember(
                family_id=family_id,
                user_id=user_id,
                role=ROLE_MEMBER,
                nickname=nickname,
                joined_at=time.time(),
            )
            session.add(member)
            session.flush()
            return member.as_dict()

    @staticmethod
    def remove_member(family_id: int, user_id: str) -> bool:
        """移除成员行；目标不存在或不在该家庭时返回 False"""
        with get_db() as session:
            member = session.scalar(
                select(FamilyMember).where(
                    FamilyMember.family_id == family_id,
                    FamilyMember.user_id == user_id,
                )
            )
            if member is None:
                return False
            session.delete(member)
            return True

    @staticmethod
    def update_settings(family_id: int, fields: dict) -> Optional[dict]:
        """按白名单更新家庭设置（allow_detail_view）；家庭不存在返回 None"""
        if not fields:
            return FamilyDAO.get(family_id)
        with get_db() as session:
            family = session.get(Family, family_id)
            if family is None:
                return None
            for key, value in fields.items():
                setattr(family, key, value)
            family.updated_at = time.time()
            session.flush()
            return family.as_dict()

    @staticmethod
    def regenerate_invite_code(family_id: int) -> Optional[str]:
        """重新生成邀请码（旧码立即失效）；家庭不存在返回 None"""
        with get_db() as session, translate_unique_violation("操作冲突，请重试"):
            family = session.get(Family, family_id)
            if family is None:
                return None
            family.invite_code = _new_invite_code()
            family.updated_at = time.time()
            session.flush()
            return family.invite_code

    @staticmethod
    def leave_and_maybe_disband(family_id: int, user_id: str) -> bool:
        """成员退出家庭；若是最后一名成员，同事务内解散（清家庭预算+成员+家庭）

        退出与自动解散拆在两个事务时，中间失败会残留「0 成员家庭 + 预算」
        ——重新加入的人只能成为普通成员，家庭永久无人可解散。单事务保证
        要么完整退出、要么完整回滚。成员不存在返回 False。
        """
        from app.db.models import Budget

        with get_db() as session:
            member = session.scalar(
                select(FamilyMember).where(
                    FamilyMember.family_id == family_id,
                    FamilyMember.user_id == user_id,
                )
            )
            if member is None:
                return False
            session.delete(member)
            session.flush()
            remaining = session.scalar(
                select(func.count())
                .select_from(FamilyMember)
                .where(FamilyMember.family_id == family_id)
            )
            if not remaining:
                session.execute(delete(Budget).where(Budget.family_id == family_id))
                family = session.get(Family, family_id)
                if family is not None:
                    session.delete(family)
            return True

    @staticmethod
    def disband_with_budgets(family_id: int) -> bool:
        """解散家庭并清除家庭预算（同一事务，中途失败整体回滚不留半态）

        此前预算清除与家庭删除分属两个事务，中间失败会留下「预算已清而
        家庭仍在」或反向的不可自愈状态。
        """
        from app.db.models import Budget

        with get_db() as session:
            family = session.get(Family, family_id)
            if family is None:
                return False
            session.execute(delete(Budget).where(Budget.family_id == family_id))
            session.execute(
                delete(FamilyMember).where(FamilyMember.family_id == family_id)
            )
            session.delete(family)
            return True

    @staticmethod
    def disband(family_id: int) -> bool:
        """解散家庭：先清成员再删家庭（同事务，避免孤儿成员行）"""
        with get_db() as session:
            family = session.get(Family, family_id)
            if family is None:
                return False
            session.execute(
                delete(FamilyMember).where(FamilyMember.family_id == family_id)
            )
            session.delete(family)
            return True
