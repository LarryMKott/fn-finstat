"""支付宝 csv 账单解析器测试（GBK/GB18030 编码，含说明前置行）"""

from pathlib import Path

import pytest

from app.parsers.alipay_parser import AlipayParser

HEADER = (
    "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,"
    "收/付款方式,交易状态,交易订单号,商家订单号,备注"
)

ROWS = [
    HEADER,
    "2024-01-01 08:30:00,餐饮美食,肯德基,,肯德基宅急送,支出,45.00,余额,交易成功,20240101A,,午餐",
    "2024-01-02 12:00:00,转账,李四,,转账,收入,200.00,余额宝,交易成功,20240102B,,",
    "2024-01-03 18:00:00,交通出行,滴滴出行,,快车,支出,15.50,余额,交易关闭,20240103C,,",
    "2024-01-04 09:00:00,话费,中国移动,,充值,支出,-30.00,余额,交易成功,20240104D,,",
    "2024-01-05 21:00:00,购物,淘宝,,服饰,,60.00,余额,交易成功,20240105E,,",
]


def build_csv(
    path: Path, encoding: str = "gb18030", rows: list[str] | None = None
) -> Path:
    lines = [
        "支付宝交易记录明细查询",
        "账号:[xxx@alipay.com]",
        "起始日期:2024-01-01 00:00:00    终止日期:2024-01-31 24:00:00",
        "---------------------------------",
        *(rows or ROWS),
    ]
    path.write_text("\r\n".join(lines) + "\r\n", encoding=encoding)
    return path


def test_parse_gb18030_csv(tmp_path: Path):
    file = build_csv(tmp_path / "bill.csv")
    records = AlipayParser().parse(file)
    # 交易关闭的流水被剔除，负金额、空收支行仍保留
    assert len(records) == 4
    expense, income, negative, no_direction = records

    assert expense["tx_type"] == "expense"
    assert expense["amount"] == 45.0
    assert expense["merchant"] == "肯德基"
    assert expense["remark"] == "午餐"
    assert expense["account"] == "alipay"
    assert expense["tx_id"] == "20240101A"

    assert income["tx_type"] == "income"
    assert income["amount"] == 200.0


def test_negative_amount_treated_as_expense(tmp_path: Path):
    file = build_csv(tmp_path / "bill.csv")
    records = AlipayParser().parse(file)
    negative = next(r for r in records if r["tx_id"] == "20240104D")
    assert negative["tx_type"] == "expense"
    assert negative["amount"] == 30.0  # 取绝对值入库


def test_missing_direction_defaults_to_transfer(tmp_path: Path):
    """收/支列有值但非收入/支出（如为空）时，正金额视为转账"""
    rows = [
        HEADER,
        "2024-01-06 10:00:00,转账,王五,,退款转回,,100.00,余额,交易成功,20240106F,,",
    ]
    file = build_csv(tmp_path / "bill.csv", rows=rows)
    (record,) = AlipayParser().parse(file)
    assert record["tx_type"] == "transfer"
    assert record["amount"] == 100.0


def test_remark_falls_back_to_product_description(tmp_path: Path):
    rows = [
        HEADER,
        "2024-01-07 10:00:00,餐饮,沙县小吃,,拌面,支出,12.00,余额,交易成功,20240107G,,",
    ]
    file = build_csv(tmp_path / "bill.csv", rows=rows)
    (record,) = AlipayParser().parse(file)
    assert record["remark"] == "拌面"


def test_utf8_sig_encoding_supported(tmp_path: Path):
    """UTF-8（带 BOM）导出的账单也应完整解析，而不是被 GBK 误解码为空结果"""
    file = build_csv(tmp_path / "bill.csv", encoding="utf-8-sig")
    records = AlipayParser().parse(file)
    assert len(records) == 4
    assert records[0]["merchant"] == "肯德基"


def test_decode_fallback_replaces_undecodable_bytes(tmp_path: Path):
    """非法字节序列按 gb18030 errors=replace 兜底，不抛异常"""
    file = tmp_path / "broken.csv"
    file.write_bytes(b"\xff\xfe\x81 invalid \x99")
    from app.parsers.csv_common import decode_csv

    assert isinstance(decode_csv(file), str)


def test_empty_and_headerless_files(tmp_path: Path):
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="gb18030")
    assert AlipayParser().parse(empty) == []

    headerless = tmp_path / "no_header.csv"
    headerless.write_text("随便一些说明\r\n没有列头\r\n", encoding="gb18030")
    assert AlipayParser().parse(headerless) == []


@pytest.mark.parametrize("closed_status", ["交易关闭", "已关闭"])
def test_closed_transactions_skipped(tmp_path: Path, closed_status: str):
    rows = [
        HEADER,
        f"2024-01-08 10:00:00,购物,某商户,,商品,支出,10.00,余额,{closed_status},20240108H,,",
    ]
    file = build_csv(tmp_path / "bill.csv", rows=rows)
    assert AlipayParser().parse(file) == []
