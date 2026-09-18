"""NAS 目录导入测试：来源识别、目录浏览与越界防护、按文件导入

来源识别覆盖四平台真实表头（前置说明行 + 列头行）、未知表头、
文件名关键字兜底与损坏文件；接口覆盖配置校验、目录浏览、
路径越界与完整导入管线（含去重）。
"""

import csv
import io
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.parsers import build_parser
from app.parsers.detect import detect_source
from app.services import nas_service

# ---- 各平台样例账单（与解析器测试同构：说明前置行 + 列头行 + 数据行）----

ALIPAY_ROWS = [
    ["支付宝交易记录明细查询"],
    [
        "交易时间",
        "交易分类",
        "交易对方",
        "对方账号",
        "商品说明",
        "收/支",
        "金额",
        "收/付款方式",
        "交易状态",
        "交易订单号",
        "商家订单号",
        "备注",
    ],
    [
        "2024-01-01 08:30:00",
        "餐饮美食",
        "肯德基",
        "",
        "肯德基宅急送",
        "支出",
        "45.00",
        "余额",
        "交易成功",
        "NAS-ALI-0001",
        "",
        "午餐",
    ],
]

JD_ROWS = [
    ["京东金融导出说明文字"],
    [
        "交易时间",
        "交易类型",
        "交易金额",
        "商品名称",
        "收/支",
        "交易状态",
        "流水号",
        "商户名称",
        "备注",
    ],
    [
        "2024-01-02 10:00:00",
        "消费",
        "23.90",
        "洗衣液",
        "支出",
        "交易成功",
        "NAS-JD-0001",
        "京东超市",
        "日百",
    ],
]

UNIONPAY_ROWS = [
    ["云闪付导出说明文字"],
    [
        "交易时间",
        "交易类型",
        "交易金额(元)",
        "交易方式",
        "收/支",
        "交易状态",
        "商户名称",
        "订单号",
        "备注",
    ],
    [
        "2024-01-03 12:00:00",
        "消费",
        "18.50",
        "二维码",
        "支出",
        "交易成功",
        "便利店",
        "NAS-UNP-0001",
        "",
    ],
]

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


def write_csv(path: Path, rows: list[list], encoding: str = "utf-8") -> Path:
    # csv 内容先写 StringIO 再落盘，规避参数路径直接 open（路径穿越）
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    path.write_text(buf.getvalue(), encoding=encoding, newline="")
    return path


def write_wechat_xlsx(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.append(["微信支付账单明细"])
    ws.append(WECHAT_HEADER)
    ws.append(
        [
            "2024-01-04 08:30:00",
            "商户消费",
            "瑞幸咖啡",
            "拿铁",
            "支出",
            "¥9.90",
            "零钱",
            "支付成功",
            "NAS-WX-0001",
            "",
            "",
        ]
    )
    wb.save(path)
    return path


# ---- 来源识别 ----


def test_detect_csv_sources(tmp_path: Path):
    cases = {
        "alipay.csv": ("alipay", ALIPAY_ROWS),
        "jd.csv": ("jd", JD_ROWS),
        "unionpay.csv": ("unionpay", UNIONPAY_ROWS),
    }
    for name, (expected, rows) in cases.items():
        assert detect_source(write_csv(tmp_path / name, rows)) == expected


def test_detect_xlsx_wechat(tmp_path: Path):
    assert detect_source(write_wechat_xlsx(tmp_path / "bill.xlsx")) == "wechat"


def test_detect_unknown_header(tmp_path: Path):
    rows = [
        ["随便什么系统导出"],
        ["日期", "摘要", "收支金额", "余额"],
        ["2024-01-01", "测试", "-10.00", "100.00"],
    ]
    assert detect_source(write_csv(tmp_path / "bank.csv", rows)) == ""


def test_detect_filename_fallback(tmp_path: Path):
    rows = [["日期", "摘要", "金额"], ["2024-01-01", "测试", "10.00"]]
    # 内容识别不出时按文件名关键字兜底，且后缀与平台必须匹配
    assert detect_source(write_csv(tmp_path / "京东金融流水.csv", rows)) == "jd"
    assert detect_source(write_csv(tmp_path / "alipay_record.csv", rows)) == "alipay"
    assert detect_source(write_csv(tmp_path / "云闪付明细.csv", rows)) == "unionpay"
    assert detect_source(write_csv(tmp_path / "微信账单.csv", rows)) == ""


def test_detect_corrupted_xlsx(tmp_path: Path):
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"not a zip file")
    assert detect_source(broken) == ""


def test_build_parser_registry():
    assert build_parser("alipay").account == "alipay"
    assert build_parser("jd").account == "jd"
    assert build_parser("unionpay").account == "unionpay"
    assert build_parser("wechat").account == "wechat"
    assert build_parser("unknown") is None


# ---- 接口：配置、浏览、导入 ----


@pytest.fixture()
def nas_root(tmp_path: Path):
    """模拟 NAS 账单目录：根目录 + 子目录，四平台样例 + 未识别文件"""
    root = tmp_path / "nas_bills"
    root.mkdir()
    write_csv(root / "alipay.csv", ALIPAY_ROWS)
    write_csv(root / "jd.csv", JD_ROWS, encoding="gb18030")
    write_csv(root / "unionpay.csv", UNIONPAY_ROWS)
    write_wechat_xlsx(root / "微信支付账单.xlsx")
    write_csv(root / "not_a_bill.csv", [["日期", "摘要", "金额"]])
    (root / "readme.txt").write_text("说明文件不展示", encoding="utf-8")
    (root / ".hidden.csv").write_text("date", encoding="utf-8")
    sub = root / "2023"
    sub.mkdir()
    write_csv(sub / "jd_old.csv", JD_ROWS)
    return root


def _put_config(client, root: Path):
    res = client.put("/api/nas/config", json={"import_dir": str(root)})
    assert res.status_code == 200
    return res.json()["data"]


def test_config_validate_and_roundtrip(client, tmp_path: Path):
    assert client.get("/api/nas/config").json()["data"]["import_dir"] == ""

    # 相对路径拒绝
    res = client.put("/api/nas/config", json={"import_dir": "relative/path"})
    assert res.status_code == 400

    data = _put_config(client, tmp_path)
    assert data["import_dir"] == str(tmp_path)
    assert data["exists"] is True
    assert data["supported_exts"] == [".csv", ".xlsx"]

    # 不存在的目录允许保存，但 exists=False 供前端提示
    data = client.put(
        "/api/nas/config", json={"import_dir": str(tmp_path / "nope")}
    ).json()["data"]
    assert data["exists"] is False


def test_files_require_config(client):
    res = client.get("/api/nas/files")
    assert res.status_code == 400
    assert "请先" in res.json()["msg"]


def test_files_list_with_sources(client, nas_root: Path):
    _put_config(client, nas_root)
    data = client.get("/api/nas/files").json()["data"]
    # 只回传目录名，不暴露服务器绝对路径（防回归：整体响应都不得含完整路径）
    assert data["root"] == nas_root.name
    assert str(nas_root) not in str(data)
    assert data["path"] == ""
    assert [d["name"] for d in data["dirs"]] == ["2023"]

    sources = {f["name"]: f["source"] for f in data["files"]}
    assert sources["alipay.csv"] == "alipay"
    assert sources["jd.csv"] == "jd"  # gb18030 编码也能识别
    assert sources["unionpay.csv"] == "unionpay"
    assert sources["微信支付账单.xlsx"] == "wechat"
    assert sources["not_a_bill.csv"] == "unknown"
    # 仅展示支持的账单后缀与目录，隐藏文件/其他后缀不出现
    assert "readme.txt" not in sources and ".hidden.csv" not in sources


def test_files_subdir_navigation(client, nas_root: Path):
    _put_config(client, nas_root)
    data = client.get("/api/nas/files", params={"path": "2023"}).json()["data"]
    assert data["parent"] == ""
    assert [f["name"] for f in data["files"]] == ["jd_old.csv"]

    # 二级目录的 parent 应指回根目录
    (nas_root / "2023" / "deep").mkdir()
    data = client.get("/api/nas/files", params={"path": "2023/deep"}).json()["data"]
    assert data["parent"] == "2023"


def test_files_reject_escape(client, nas_root: Path):
    _put_config(client, nas_root)
    res = client.get("/api/nas/files", params={"path": "../../"})
    assert res.status_code == 400
    assert "超出" in res.json()["msg"]

    # 绝对路径同样越界
    res = client.get("/api/nas/files", params={"path": "C:/Windows"})
    assert res.status_code == 400


def test_files_missing_dir(client, tmp_path: Path):
    _put_config(client, tmp_path)
    res = client.get("/api/nas/files", params={"path": "nope"})
    assert res.status_code == 400


def test_import_file_end_to_end(client, nas_root: Path):
    _put_config(client, nas_root)
    res = client.post("/api/nas/import", json={"path": "alipay.csv"})
    assert res.status_code == 200
    result = res.json()["data"]
    assert result["total"] == 1 and result["inserted"] == 1

    # 重复导入按交易单号去重
    again = client.post("/api/nas/import", json={"path": "alipay.csv"}).json()["data"]
    assert again["inserted"] == 0 and again["skipped"] == 1

    # 子目录文件与 xlsx 同样可导入
    assert (
        client.post("/api/nas/import", json={"path": "2023/jd_old.csv"}).status_code
        == 200
    )
    assert (
        client.post("/api/nas/import", json={"path": "微信支付账单.xlsx"}).status_code
        == 200
    )


def test_import_rejects_unknown_and_missing(client, nas_root: Path):
    _put_config(client, nas_root)
    res = client.post("/api/nas/import", json={"path": "not_a_bill.csv"})
    assert res.status_code == 400
    assert "无法识别" in res.json()["msg"]

    res = client.post("/api/nas/import", json={"path": "ghost.csv"})
    assert res.status_code == 404

    # 越界导入同样拒绝
    res = client.post("/api/nas/import", json={"path": "../outside.csv"})
    assert res.status_code == 400


def test_import_empty_file(client, nas_root: Path):
    (nas_root / "empty.csv").write_text("", encoding="utf-8")
    _put_config(client, nas_root)
    res = client.post("/api/nas/import", json={"path": "empty.csv"})
    assert res.status_code == 400
    assert "内容为空" in res.json()["msg"]


def test_import_size_limit(client, nas_root: Path, monkeypatch):
    # 大小上限取自 config 常量，测试里调小以避免真实生成 10MB 文件
    monkeypatch.setattr(nas_service, "NAS_MAX_FILE_SIZE", 10)
    _put_config(client, nas_root)
    res = client.post("/api/nas/import", json={"path": "alipay.csv"})
    assert res.status_code == 400
    assert "10MB" in res.json()["msg"]
