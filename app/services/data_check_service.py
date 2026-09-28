"""记账数据体检（AI-2，脑洞清单）：把「数据质量」变成一次可执行的检查

产品定位：未分类与脏数据是 AI 归类准确率的最大噪声源（分类、报表、固定项
与订阅识别都受影响）。体检为**纯只读统计、零 AI 成本**；能被流水页筛选
表达的项（未分类、疑似重复组）携带 jump 字段——前端经 T-6.2 的
billsFilterHandoff 交接机制一键跳转到对应筛选；筛选表达不了的项（商户为空、
金额为 0）如实给计数与提示，不硬跳。

与脑洞清单 AI-2 原文的两处口径对齐：
- 「未归属账本的历史数据」：T-7.1 落地后 ledger_id NOT NULL 且迁移已回填，
  「无账本」已不存在——实现为「无归属账号的流水」（user_id 为空串，仅
  fnOS 网关多账号模式有意义；本地模式当前账号即空串，跳过该项）；
- 「长期未记账的月份」：自 max(首个记账月, 近 6 个月窗口起点) 到当前月，
  其中没有任何流水的月份（新用户不倒查开户前的月份，当前月未记账也算提醒）。
"""

from datetime import date

from app.config import DEFAULT_CATEGORY
from app.db.dao.bill_dao import BillDAO
from app.utils.period import shift_month

STALE_WINDOW_MONTHS = 6
MAX_DUPLICATE_GROUPS = 3


def _month_index(month: str) -> int:
    year, mon = month.split("-")
    return int(year) * 12 + int(mon)


def data_check(user_id: str, today: date | None = None) -> dict:
    """全项体检（只读）：返回逐项计数、提示与可跳转的筛选口径

    items 中每项 = {key, label, count, hint, jump?, detail?}；
    count 为 0 的项同样返回（前端据此渲染全绿态），jump 仅在 count > 0
    且筛选可表达时给出。
    """
    today = today or date.today()
    cur = f"{today.year:04d}-{today.month:02d}"
    counts = BillDAO.data_check_counts(user_id)

    items = [
        {
            "key": "uncategorized",
            "label": "未分类流水",
            "count": counts["uncategorized"],
            "hint": f"分类为「{DEFAULT_CATEGORY}」的流水是 AI 归类与报表的主要噪声源",
            "jump": (
                {"categories": [DEFAULT_CATEGORY]} if counts["uncategorized"] else None
            ),
        },
        {
            "key": "missing_merchant",
            "label": "商户名为空",
            "count": counts["missing_merchant"],
            "hint": "空商户名会影响商户排行、固定项与订阅识别，建议补齐",
            "jump": None,  # 流水筛选表达不了「商户为空」，如实只给计数
        },
        {
            "key": "zero_amount",
            "label": "金额为 0 的记录",
            "count": counts["zero_amount"],
            "hint": "金额为 0 多为导入残留或误记，建议核实后删除",
            "jump": None,
        },
    ]

    # 长期未记账的月份：从首个记账月与窗口起点中较晚者起，逐月查缺
    empty_months: list[str] = []
    first = counts["first_month"]
    if first:
        window_start = max(first, shift_month(cur, -(STALE_WINDOW_MONTHS - 1)))
        m = window_start
        while _month_index(m) <= _month_index(cur):
            if m not in counts["months"]:
                empty_months.append(m)
            m = shift_month(m, 1)
    items.append(
        {
            "key": "stale_months",
            "label": "未记账月份",
            "count": len(empty_months),
            "hint": "这些月份没有任何流水，统计与预测的样本由此失真",
            "jump": None,
            "detail": empty_months,
        }
    )

    dup_total, dup_top = BillDAO.duplicate_groups(user_id, MAX_DUPLICATE_GROUPS)
    items.append(
        {
            "key": "duplicates",
            "label": "疑似重复导入",
            "count": dup_total,
            "hint": "同日同商户同金额的重复流水（每组可一键筛出核实）",
            "jump": None,  # 跳转口径在 detail[] 各组的 jump 上
            "detail": [
                {
                    **g,
                    "jump": {
                        "start": g["day"],
                        "end": g["day"],
                        "merchants": [g["merchant"]],
                    },
                }
                for g in dup_top
            ],
        }
    )

    # 无归属账号的流水：仅网关多账号模式有意义（本地模式当前账号即空串）
    if user_id:
        unowned = BillDAO.count_unassigned()
        if unowned:
            items.append(
                {
                    "key": "unowned",
                    "label": "无归属账号的流水",
                    "count": unowned,
                    "hint": "升级/换库遗留的历史数据，任何账号都看不到，"
                    "可在 设置 → 数据库 迁移认领",
                    "jump": None,
                }
            )

    return {
        "checked_month": cur,
        "items": items,
        "problem_count": sum(1 for i in items if i["count"] > 0),
        "problem_total": sum(i["count"] for i in items),
    }
