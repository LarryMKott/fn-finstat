"""微信支付 xlsx 账单解析器测试"""

from pathlib import Path

from openpyxl import Workbook

from app.parsers.wechat_parser import WechatParser

WECHAT_HEADER = [
    "交易时间",
    "交易类型",
    "交易对方",
    "商品",
    "收/支",
    "金额(元)",
    "支付方式",
    "当前状态",
    "交易单号",
    "商户单号",
    "备注",
]


def build_xlsx(path: Path, rows: list[list]) -> Path:
    wb = Workbook()
    ws = wb.active
    # 模拟微信导出账单的前置说明行
    ws.append(["微信支付账单明细"])
    ws.append(["----------------------微信支付账单明细----------------------"])
    ws.append(["导出时间：2024-07-01 00:00:00"])
    for row in rows:
        ws.append(row)
    wb.save(path)
    return path


def test_parse_expense_income_transfer(tmp_path: Path):
    file = build_xlsx(
        tmp_path / "bill.xlsx",
        [
            WECHAT_HEADER,
            [
                "2024-01-01 08:30:00",
                "商户消费",
                "瑞幸咖啡",
                "拿铁",
                "支出",
                "¥9.90",
                "零钱",
                "支付成功",
                "10001",
                "M01",
                "",
            ],
            [
                "2024-01-02 12:00:00",
                "微信红包",
                "张三",
                "红包",
                "收入",
                "¥88.00",
                "零钱",
                "已存入零钱",
                "10002",
                "",
                "",
            ],
            [
                "2024-01-03 09:00:00",
                "零钱充值",
                "/",
                "充值",
                "/",
                "¥500.00",
                "招商银行",
                "充值成功",
                "10003",
                "",
                "",
            ],
        ],
    )
    records = WechatParser().parse(file)
    assert len(records) == 3
    expense, income, transfer = records

    assert expense["tx_type"] == "expense"
    assert expense["amount"] == 9.9
    assert expense["merchant"] == "瑞幸咖啡"
    assert expense["category"] == ""  # 留空由服务层自动归类
    assert expense["account"] == "wechat"
    assert expense["tx_id"] == "10001"

    assert income["tx_type"] == "income"
    assert income["amount"] == 88.0

    assert transfer["tx_type"] == "transfer"
    assert transfer["amount"] == 500.0


def test_amount_with_thousand_separator(tmp_path: Path):
    file = build_xlsx(
        tmp_path / "bill.xlsx",
        [
            WECHAT_HEADER,
            [
                "2024-01-01 08:30:00",
                "商户消费",
                "京东商城",
                "手机",
                "支出",
                "￥1,234.56",
                "零钱",
                "支付成功",
                "20001",
                "",
                "",
            ],
        ],
    )
    (record,) = WechatParser().parse(file)
    assert record["amount"] == 1234.56


def test_invalid_rows_skipped(tmp_path: Path):
    file = build_xlsx(
        tmp_path / "bill.xlsx",
        [
            WECHAT_HEADER,
            [
                "2024-01-01 08:30:00",
                "商户消费",
                "瑞幸咖啡",
                "拿铁",
                "支出",
                "¥9.90",
                "零钱",
                "支付成功",
                "30001",
                "",
                "",
            ],
            [
                "2024-01-02 08:30:00",
                "商户消费",
                "无金额",
                "",
                "支出",
                "",
                "零钱",
                "支付成功",
                "30002",
                "",
                "",
            ],
            [
                "2024-01-03 08:30:00",
                "商户消费",
                "零金额",
                "",
                "支出",
                "¥0.00",
                "零钱",
                "支付成功",
                "30003",
                "",
                "",
            ],
            [
                "2024-01-04 08:30:00",
                "商户消费",
                "负金额",
                "",
                "支出",
                "¥-5.00",
                "零钱",
                "支付成功",
                "30004",
                "",
                "",
            ],
            [
                "",
                "",
                "缺交易时间",
                "",
                "支出",
                "¥5.00",
                "零钱",
                "支付成功",
                "30005",
                "",
                "",
            ],
            [""],
        ],
    )
    records = WechatParser().parse(file)
    assert len(records) == 1
    assert records[0]["tx_id"] == "30001"


def test_no_header_returns_empty(tmp_path: Path):
    file = build_xlsx(tmp_path / "bill.xlsx", [["随便一些说明文字"], ["没有列头行"]])
    assert WechatParser().parse(file) == []


def test_empty_file_returns_empty(tmp_path: Path):
    wb = Workbook()
    wb.save(tmp_path / "empty.xlsx")
    assert WechatParser().parse(tmp_path / "empty.xlsx") == []
