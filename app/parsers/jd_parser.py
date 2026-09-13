"""京东金融账单（.csv）解析器

京东金融 App 导出的交易流水 CSV 前几行为说明文字，随后是列头行（列名随版本略有差异）：
    交易时间,交易类型,交易金额,商品名称,收/支,交易状态,流水号,商户名称,备注
编码 utf-8 / gb18030 自适应，经 TolerantCsvParser 按列头别名宽容匹配。
"""

from app.parsers.base import direction_to_type
from app.parsers.csv_common import TolerantCsvParser, clean_amount

_SKIP_STATUS = {"交易关闭", "已关闭", "退款成功后关闭", "已取消", "失败"}


class JdParser(TolerantCsvParser):
    """京东账单 csv 解析器"""

    account = "jd"

    REQUIRED = ("tx_time", "amount")
    ALIASES = {
        "tx_time": ("交易时间", "交易日期"),
        "amount": ("交易金额", "金额", "金额(元)", "交易金额(元)"),
        "direction": ("收/支", "收支"),
        "goods": ("商品名称", "商品", "商品描述"),
        "merchant": ("商户名称", "交易对方", "商户"),
        "status": ("交易状态", "订单状态", "状态"),
        "tx_id": ("流水号", "交易单号", "交易流水号", "订单号", "交易订单号"),
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

        # 收/支列缺失时视为转账（不计收支）
        tx_type = direction_to_type(row.get("direction", "").strip()) or "transfer"

        merchant = row.get("merchant", "").strip()
        remark = row.get("remark", "").strip() or row.get("goods", "").strip()
        return {
            "tx_time": tx_time,
            "account": self.account,
            "tx_type": tx_type,
            "merchant": merchant or remark[:50],
            "amount": amount,
            "category": "",
            "tx_id": row.get("tx_id", "").strip(),
            "remark": remark,
        }
