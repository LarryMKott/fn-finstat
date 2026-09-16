"""京东 / 云闪付账单解析器测试：列头别名匹配、编码探测、状态过滤与收支推断"""

from pathlib import Path

import pytest

from app.parsers.jd_parser import JdParser
from app.parsers.unionpay_parser import UnionPayParser

JD_HEADER = [
    "交易时间",
    "交易类型",
    "交易金额",
    "商品名称",
    "收/支",
    "交易状态",
    "流水号",
    "商户名称",
    "备注",
]

UNIONPAY_HEADER = [
    "交易时间",
    "交易类型",
    "交易金额(元)",
    "交易方式",
    "收/支",
    "交易状态",
    "商户名称",
    "订单号",
    "备注",
]


def write_csv(
    tmp_path: Path, name: str, rows: list[list], encoding: str = "utf-8"
) -> Path:
    """用 csv.writer 写文件（含引号转义），验证解析器能处理带引号的单元格"""
    import csv
    import io

    # 只取文件名部分限制在 tmp_path 下，规避参数路径直接 open 落盘（路径穿越）
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    path = tmp_path / Path(name).name
    path.write_text(buf.getvalue(), encoding=encoding, newline="")
    return path


def test_jd_parser_basic(tmp_path):
    rows = [
        ["京东金融导出说明文字"],
        JD_HEADER,
        [
            "2026-01-02 10:00:00",
            "消费",
            "23.90",
            "洗衣液",
            "支出",
            "交易成功",
            "JD1001",
            "京东超市",
            "日百",
        ],
        [
            "2026-01-03 11:30:00",
            "退款",
            "5.00",
            "洗衣液",
            "收入",
            "交易成功",
            "JD1002",
            "京东超市",
            "退款",
        ],
    ]
    path = write_csv(tmp_path, "jd.csv", rows)
    records = JdParser().parse(path)
    assert len(records) == 2
    first, second = records
    assert first["account"] == "jd"
    assert first["tx_type"] == "expense"
    assert first["amount"] == 23.9
    assert first["tx_id"] == "JD1001"
    assert first["merchant"] == "京东超市"
    assert first["remark"] == "日百"
    assert second["tx_type"] == "income"


def test_jd_parser_skips_closed_and_bad_rows(tmp_path):
    rows = [
        JD_HEADER,
        [
            "2026-01-02 10:00:00",
            "消费",
            "10.00",
            "商品",
            "支出",
            "交易关闭",
            "JD2001",
            "店",
            "",
        ],
        ["", "", "", "", "", "", "", "", ""],
        [
            "2026-01-03 10:00:00",
            "消费",
            "abc",
            "商品",
            "支出",
            "交易成功",
            "JD2002",
            "店",
            "",
        ],
        [
            "2026-01-04 10:00:00",
            "消费",
            "¥1,299.00",
            "手机",
            "支出",
            "交易成功",
            "JD2003",
            "京东",
            "",
        ],
    ]
    records = JdParser().parse(write_csv(tmp_path, "jd2.csv", rows))
    assert len(records) == 1
    assert records[0]["amount"] == 1299.0
    assert records[0]["tx_id"] == "JD2003"


def test_jd_parser_gbk_and_missing_direction(tmp_path):
    rows = [
        JD_HEADER,
        [
            "2026-01-05 12:00:00",
            "转账",
            "88.50",
            "白条还款",
            "不计收支",
            "交易成功",
            "JD3001",
            "京东金融",
            "",
        ],
    ]
    records = JdParser().parse(write_csv(tmp_path, "jd3.csv", rows, encoding="gb18030"))
    assert len(records) == 1
    assert records[0]["tx_type"] == "transfer"


def test_jd_parser_merchant_falls_back_to_goods(tmp_path):
    rows = [
        JD_HEADER,
        [
            "2026-01-06 09:00:00",
            "消费",
            "3.00",
            "矿泉水",
            "支出",
            "交易成功",
            "JD4001",
            "",
            "",
        ],
    ]
    records = JdParser().parse(write_csv(tmp_path, "jd4.csv", rows))
    assert records[0]["merchant"] == "矿泉水"


def test_unionpay_parser_basic(tmp_path):
    rows = [
        ["云闪付导出说明"],
        UNIONPAY_HEADER,
        [
            "2026-02-01 08:30:00",
            "消费",
            "15.50",
            "余额",
            "支出",
            "交易成功",
            "公交卡充值",
            "UP1001",
            "",
        ],
        [
            "2026-02-02 09:00:00",
            "退款",
            "7.50",
            "余额",
            "收入",
            "交易成功",
            "商家退款",
            "UP1002",
            "活动返现",
        ],
    ]
    records = UnionPayParser().parse(write_csv(tmp_path, "up.csv", rows))
    assert len(records) == 2
    first, second = records
    assert first["account"] == "unionpay"
    assert first["tx_type"] == "expense"
    assert first["amount"] == 15.5
    assert first["tx_id"] == "UP1001"
    assert second["tx_type"] == "income"


def test_unionpay_parser_infers_direction_from_kind(tmp_path):
    """缺「收/支」列时按交易类型关键字推断"""
    header = [
        "交易时间",
        "交易类型",
        "交易金额(元)",
        "交易方式",
        "交易状态",
        "商户名称",
        "订单号",
    ]
    rows = [
        header,
        [
            "2026-02-03 10:00:00",
            "消费",
            "20.00",
            "银行卡",
            "交易成功",
            "超市",
            "UP2001",
        ],
        ["2026-02-04 10:00:00", "退款", "8.00", "银行卡", "交易成功", "超市", "UP2002"],
        [
            "2026-02-05 10:00:00",
            "转账",
            "100.00",
            "银行卡",
            "交易成功",
            "转账",
            "UP2003",
        ],
    ]
    records = UnionPayParser().parse(write_csv(tmp_path, "up2.csv", rows))
    assert [r["tx_type"] for r in records] == ["expense", "income", "transfer"]


@pytest.mark.parametrize("parser_cls", [JdParser, UnionPayParser])
def test_parser_requires_header_row(tmp_path, parser_cls):
    """没有表头行（必需列匹配不上）时解析为空列表而不是崩溃"""
    rows = [
        ["随便一行", "内容"],
        ["2026-01-01", "10.00"],
    ]
    records = parser_cls().parse(write_csv(tmp_path, "bad.csv", rows))
    assert records == []
