"""云闪付（银联）账单（.csv）解析器

云闪付 App 导出的交易明细 CSV 前几行为说明文字，随后是列头行（列名随版本略有差异）：
    交易时间,交易类型,交易金额(元),交易方式,收/支,交易状态,商户名称,订单号,备注
编码 utf-8 / gb18030 自适应，经 TolerantCsvParser 按列头别名宽容匹配。

收/支列缺失时按「交易类型」关键字推断：含「退款/收入」记收入、含「消费/支出」记支出，
其余记转账。
"""

from app.parsers.base import direction_to_type
from app.parsers.csv_common import TolerantCsvParser, clean_amount

_SKIP_STATUS = {"交易关闭", "已关闭", "已取消", "失败", "已退货"}

# 交易类型关键字 → 收支类型（收/支列缺失时的推断依据）
_TYPE_HINTS = (
    ("退款", "income"),
    ("收入", "income"),
    ("消费", "expense"),
    ("支出", "expense"),
)


class UnionPayParser(TolerantCsvParser):
    """云闪付账单 csv 解析器"""

    account = "unionpay"

    REQUIRED = ("tx_time", "amount")
    ALIASES = {
        "tx_time": ("交易时间", "交易日期"),
        "amount": ("交易金额(元)", "交易金额", "金额(元)", "金额"),
        "direction": ("收/支", "收支"),
        "tx_kind": ("交易类型", "交易类别"),
        "merchant": ("商户名称", "交易对方", "商户", "收付款对象"),
        "status": ("交易状态", "订单状态", "状态"),
        "tx_id": ("订单号", "交易单号", "交易流水号", "流水号"),
        "remark": ("备注",),
    }
    SKIP_STATUS = _SKIP_STATUS

    def _row_to_record(self, row: dict[str, str]) -> dict | None:
        tx_time = row.get("tx_time", "").strip()
        if not tx_time:
            return None
        if row.get("status", "").strip() in self.SKIP_STATUS:
            return None
        amount = clean_amount(row.get("amount", ""))
        if amount is None:
            return None

        direction = direction_to_type(row.get("direction", "").strip())
        if direction:
            tx_type = direction
        else:
            # 收/支列缺失时按「交易类型」关键字推断（含「退款/收入」记收入、
            # 「消费/支出」记支出），其余记转账
            kind = row.get("tx_kind", "")
            tx_type = "transfer"
            for keyword, inferred in _TYPE_HINTS:
                if keyword in kind:
                    tx_type = inferred
                    break

        merchant = row.get("merchant", "").strip()
        remark = row.get("remark", "").strip()
        return {
            "tx_time": tx_time,
            "account": self.account,
            "tx_type": tx_type,
            "merchant": merchant,
            "amount": amount,
            "category": "",
            "tx_id": row.get("tx_id", "").strip(),
            "remark": remark,
        }
