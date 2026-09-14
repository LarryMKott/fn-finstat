"""消费地图接口自检：临时库造流水 → 调 region_map → 校验聚合与识别率

用法：
    app/venv/Scripts/python.exe scripts/verify_region_map.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import tempfile  # noqa: E402

from app.api import stat  # noqa: E402
from app.config import DEFAULT_CATEGORIES, DBSettings  # noqa: E402
from app.db.base import (  # noqa: E402
    LATEST_SCHEMA_VERSION,
    _STATE,
    insert_ignore_rows,
    set_schema_version,
)
from app.db.dao.bill_dao import BillDAO  # noqa: E402
from app.db.models import Base, Category  # noqa: E402

USER = "10001"

ROWS = [
    # (商户, 备注, 金额)  —— 成都 3 笔共 300
    ("海底捞火锅(春熙路店)", "", 200.0),
    ("成都地铁", "", 4.5),
    ("成都市第一人民医院", "", 95.5),
    # 北京 1 笔 500
    ("国贸大厦(北京)", "", 500.0),
    # 上海 2 笔 210
    ("上海静安寺苹果店", "", 200.0),
    ("南京路步行街", "", 10.0),
    # 未识别 2 笔 40
    ("美团外卖", "", 30.0),
    ("腾讯视频", "", 10.0),
    # 收入不计入地图
    ("公司", "工资", 9999.0),
]


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    engine = create_engine(f"sqlite:///{(tmp / 't.db').as_posix()}")
    Base.metadata.create_all(engine)
    _STATE.activate(DBSettings(db_type="sqlite"), engine)
    with engine.begin() as conn:
        insert_ignore_rows(
            conn, Category.__table__, [{"name": n} for n in DEFAULT_CATEGORIES]
        )
    with Session(engine) as s:
        set_schema_version(s, LATEST_SCHEMA_VERSION)
        s.commit()

    records = []
    for i, (merchant, remark, amount) in enumerate(ROWS):
        records.append(
            {
                "tx_time": f"2026-09-{i + 1:02d} 12:00:00",
                "account": "wechat",
                "tx_type": "income" if amount > 1000 else "expense",
                "merchant": merchant,
                "amount": amount,
                "category": "其他",
                "tx_id": f"RM-{i:03d}",
                "remark": remark,
            }
        )
    BillDAO.insert_many(records, USER)

    app = FastAPI()
    app.include_router(stat.router)
    client = TestClient(app)
    data = client.get("/api/stat/region_map", headers={"X-Trim-Userid": USER}).json()

    print("max_value      :", data["max_value"])
    print("total_amount   :", data["total_amount"])
    print("matched_amount :", data["matched_amount"])
    print("matched_rate   :", data["matched_rate"])
    print("total_count    :", data["total_count"])
    print("scanned_count  :", data["scanned_count"])
    print("truncated      :", data["truncated"])
    print("\nprovinces:")
    for p in data["provinces"]:
        print(f"  {p['name']:<14} {p['value']:>8}  x{p['count']}")
    print("\ncities:")
    for c in data["cities"]:
        print(
            f"  {c['name']:<8} {c['province']:<14} {c['value']:>8}  x{c['count']}"
            f"  coord={c.get('coord')}"
        )

    checks = [
        ("支出总额 1050", data["total_amount"] == 1050.0),
        ("识别金额 1010", data["matched_amount"] == 1010.0),
        ("识别率 96%", data["matched_rate"] == 96),
        ("参与条数 8（收入被排除）", data["scanned_count"] == 8),
        ("省份数 3", len(data["provinces"]) == 3),
        (
            "最高省份为北京 500",
            data["provinces"][0]["name"] == "北京市"
            and data["provinces"][0]["value"] == 500.0,
        ),
        (
            "四川省 300",
            any(
                p["name"] == "四川省" and p["value"] == 300.0 for p in data["provinces"]
            ),
        ),
        (
            "上海市 210",
            any(
                p["name"] == "上海市" and p["value"] == 210.0 for p in data["provinces"]
            ),
        ),
        (
            "城市含成都",
            any(c["name"] == "成都" and c["value"] == 300.0 for c in data["cities"]),
        ),
        (
            "成都归属四川省",
            any(
                c["name"] == "成都" and c["province"] == "四川省"
                for c in data["cities"]
            ),
        ),
        # 气泡图依赖：每个城市都要有经纬度，否则前端无法打点
        (
            "城市均带坐标（经度 73~136 / 纬度 3~54）",
            all(
                isinstance(c.get("coord"), list)
                and len(c["coord"]) == 2
                and 73 <= c["coord"][0] <= 136
                and 3 <= c["coord"][1] <= 54
                for c in data["cities"]
            ),
        ),
        (
            "成都坐标落在四川盆地",
            next((c["coord"] for c in data["cities"] if c["name"] == "成都"), None)
            == [104.065735, 30.659462],
        ),
    ]
    print()
    failed = 0
    for label, ok in checks:
        print(f"  {'OK  ' if ok else 'FAIL'} {label}")
        failed += 0 if ok else 1
    print(f"\n通过 {len(checks) - failed}/{len(checks)}")
    engine.dispose()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
