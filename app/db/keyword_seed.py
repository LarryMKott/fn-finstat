"""内置关键词播种（v1.1）：把 category_matcher.RULES 灌入 category_keywords 表

设计口径（D-3 推荐项）：内置 RULES 播种进关键词表后即退出匹配链，关键词表
成为关键词匹配的唯一数据源——内置词在界面可见、可停用，与 AI / 手工词同权。

两条触发路径（共用本模块的幂等实现）：
- 老库升级：migrations._v16_add_category_extension 在迁移事务内调用（该库的
  categories 已存在，直接按名对号入座）
- 全新安装：不经过任何迁移（建表即最新版本），init_db 在预置默认分类之后
  调用兜底

app_meta.keywords_seeded 守卫保证只播一次；分类缺失时跳过该组词（分类预置
是 init_db 的职责，本模块不做补建）。
"""

import time

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import AppMeta, Category, CategoryKeyword
from app.db.engine import insert_ignore_rows
from app.utils.category_matcher import RULES

KEYWORDS_SEEDED_KEY = "keywords_seeded"


def keywords_seeded(session: Session) -> bool:
    """是否已完成过内置词播种（app_meta 守卫位）"""
    return (
        session.scalar(
            select(AppMeta.meta_value).where(AppMeta.meta_key == KEYWORDS_SEEDED_KEY)
        )
        is not None
    )


def ensure_builtin_keywords(session: Session) -> int:
    """把内置 RULES 播种进关键词表（幂等，事务随调用方提交），返回播种词数

    词与 RULES 逐字一致，老用户升级后匹配行为等价；同名词（跨分类重复或有
    残留）由唯一键 + OR IGNORE 跳过，不报错。
    """
    if keywords_seeded(session):
        return 0
    name_to_id = dict(session.execute(select(Category.name, Category.id)).all())
    now = time.time()
    rows = []
    for category, words in RULES.items():
        category_id = name_to_id.get(category)
        if category_id is None:
            continue
        for word in words:
            rows.append(
                {
                    "category_id": category_id,
                    "keyword": word,
                    "source": "builtin",
                    "enabled": True,
                    "created_at": now,
                }
            )
    if rows:
        insert_ignore_rows(session.connection(), CategoryKeyword.__table__, rows)
    session.execute(delete(AppMeta).where(AppMeta.meta_key == KEYWORDS_SEEDED_KEY))
    session.add(AppMeta(meta_key=KEYWORDS_SEEDED_KEY, meta_value="1"))
    session.flush()
    return len(rows)


def reset_and_seed(session: Session) -> int:
    """清除播种守卫并重新播种（replace 恢复 v7 之前备份后重建内置词用）

    该场景下 category_keywords 表刚被清空、守卫位仍是已播种状态，直接调
    ensure_builtin_keywords 会被守卫拦住，必须先摘守卫。
    """
    session.execute(delete(AppMeta).where(AppMeta.meta_key == KEYWORDS_SEEDED_KEY))
    session.flush()
    return ensure_builtin_keywords(session)
