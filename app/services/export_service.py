"""导出文件构建：流水行 → xlsx / CSV 字节流（导出列与解析导入字段对应）"""

import csv
import io

from openpyxl import Workbook

from app.core.constants import ACCOUNT_LABELS, TX_TYPE_LABELS

# 导出列定义（表头 → 取值键），xlsx 与 CSV 共用同一份顺序
EXPORT_COLUMNS = [
    ("交易时间", "tx_time"),
    ("账户", "account"),
    ("类型", "tx_type_label"),
    ("商户/交易对方", "merchant"),
    ("金额(元)", "amount"),
    ("分类", "category"),
    ("标签", "tags"),
    ("报销", "reimbursed_label"),
    ("交易单号", "tx_id"),
    ("备注", "remark"),
]


def _export_rows(bills: list[dict]) -> list[list]:
    """把流水字典转为按 EXPORT_COLUMNS 顺序排列的二维表（首行为表头）"""
    header = [c[0] for c in EXPORT_COLUMNS]
    rows = [header]
    for b in bills:
        account = ACCOUNT_LABELS.get(b.get("account"), b.get("account"))
        rows.append(
            [
                b.get("tx_time", ""),
                account,
                TX_TYPE_LABELS.get(b.get("tx_type"), b.get("tx_type", "")),
                b.get("merchant", ""),
                b.get("amount", 0),
                b.get("category", ""),
                b.get("tags", ""),
                "是" if b.get("reimbursed") else "",
                b.get("tx_id") or "",
                b.get("remark", ""),
            ]
        )
    return rows


def build_xlsx(bills: list[dict]) -> bytes:
    """构建 xlsx（openpyxl 内存构建）；金额写为数值便于 Excel 二次统计"""
    wb = Workbook()
    ws = wb.active
    ws.title = "流水"
    rows = _export_rows(bills)
    for i, row in enumerate(rows):
        ws.append(row)
    # 简单设宽：金额/时间列加宽，避免默认宽度挤成 ####
    for idx, width in ((1, 20), (4, 24), (5, 12), (9, 24)):
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = width
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def csv_safe(value) -> str:
    """防 CSV 公式注入：Excel 打开时 = + - @ 开头的单元格会被按公式求值，前缀单引号使其按文本显示"""
    text = str(value)
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def build_csv(bills: list[dict]) -> bytes:
    """构建 CSV（utf-8-sig 带 BOM，Excel 直接打开不乱码）

    仅 CSV 需要防公式注入；xlsx 经 openpyxl 写入的是字符串单元格，无此风险。
    金额列（下标 4）保持数值原样，避免破坏 Excel 二次统计。
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in _export_rows(bills):
        writer.writerow(
            [cell if i == 4 else csv_safe(cell) for i, cell in enumerate(row)]
        )
    return buf.getvalue().encode("utf-8-sig")
