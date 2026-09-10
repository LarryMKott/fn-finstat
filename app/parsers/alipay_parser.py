"""支付宝账单（.csv，GBK 编码）解析器

支付宝导出的“交易流水证明” CSV 前几行为说明文字，随后是列头行：
    交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注
文件编码为 GBK/GB18030，自动探测解码。
"""
import csv
import io
from pathlib import Path

from app.parsers.base import BaseParser
from app.utils.amount import normalize_amount

_ENCODINGS = ("gb18030", "utf-8-sig", "utf-8")
# 交易关闭/已关闭等失败流水不计入
_SKIP_STATUS = {"交易关闭", "已关闭"}


class AlipayParser(BaseParser):
    """支付宝 csv 账单解析器"""

    account = "alipay"

    def parse(self, file_path: Path) -> list[dict]:
        text = self._decode(file_path)
        reader = csv.reader(io.StringIO(text))
        header: list[str] | None = None
        records: list[dict] = []
        for row in reader:
            if not row:
                continue
            cells = [c.strip() for c in row]
            if header is None:
                # 列头行：第一列为“交易时间”
                if cells and cells[0].startswith("交易时间"):
                    header = cells
                continue
            record = self._row_to_record(dict(zip(header, cells)))
            if record:
                records.append(record)
        return records

    @staticmethod
    def _decode(file_path: Path) -> str:
        raw = file_path.read_bytes()
        for enc in _ENCODINGS:
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("gb18030", errors="replace")

    def _row_to_record(self, row: dict) -> dict | None:
        tx_time = row.get("交易时间", "").strip()
        if not tx_time:
            return None
        status = row.get("交易状态", "").strip()
        if status in _SKIP_STATUS:
            return None

        tx_id = row.get("交易订单号", "").strip()
        amount_text = row.get("金额", "").strip().replace(",", "").replace("¥", "").replace("￥", "")
        if not amount_text:
            return None
        try:
            amount = normalize_amount(abs(float(amount_text)))
        except ValueError:
            return None
        if amount <= 0:
            return None

        direction = row.get("收/支", "").strip()
        if direction == "收入":
            tx_type = "income"
        elif direction == "支出":
            tx_type = "expense"
        else:
            # 收/支列缺失时按金额符号推断；否则视为转账
            tx_type = "expense" if amount_text.startswith("-") else "transfer"

        return {
            "tx_time": tx_time,
            "account": self.account,
            "tx_type": tx_type,
            "merchant": row.get("交易对方", "").strip(),
            "amount": amount,
            "category": "",
            "tx_id": tx_id,
            "remark": row.get("备注", "").strip() or row.get("商品说明", "").strip(),
        }
