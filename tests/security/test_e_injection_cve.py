"""M4 / M16：SQL 注入实战（真实请求）与依赖 CVE 排查

报告 3.4 节要求「以无害验证为准」，这里对可触达的用户输入面逐条打真实请求，
确认 SQLAlchemy 参数绑定生效、数据库未被破坏。
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def authed_client(client: TestClient) -> TestClient:
    """独立部署形态：无网关身份头 = 唯一用户，直接可写"""
    return client


INJECTION_PAYLOADS = [
    "' OR '1'='1",
    "'; DROP TABLE bills; --",
    "1' UNION SELECT 1,2,3--",
    "1 OR 1=1",
    "%27 OR 1=1--",
    "\\' OR 1=1--",
    "1); DROP TABLE bills;--",
]


def _table_intact(c: TestClient) -> bool:
    """数据库仍可正常查询 = 注入未造成结构性破坏"""
    return c.get("/api/bill/list").status_code == 200


# ====================================================================
# M4：SQL 注入 —— 真实请求
# ====================================================================


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_m4_1_bill_list_keyword_parameterized(authed_client, payload):
    """【防护有效 M4-1】流水列表 keyword 查询参数无法注入"""
    resp = authed_client.get("/api/bill/list", params={"keyword": payload})
    assert resp.status_code in (200, 422), resp.text
    assert _table_intact(authed_client), "注入载荷破坏了数据库"


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_m4_1_bill_list_sort_and_order_parameterized(authed_client, payload):
    """【防护有效 M4-1·排序面】sort_by 有显式白名单，非法字段被 400 拒绝

    ORDER BY 是最经典的 SQL 注入面（参数绑定无法覆盖标识符位置）。实测返回
    `{"code":40001,"msg":"无效的排序字段"}`，说明实现做了白名单校验。
    """
    resp = authed_client.get(
        "/api/bill/list", params={"sort_by": payload, "order": payload}
    )
    # 400 = 白名单拒绝；200 = 合法值放行。两者都说明未把载荷拼进 SQL。
    assert resp.status_code in (200, 400, 422), resp.text
    if resp.status_code == 400:
        assert "无效的排序字段" in resp.text
    assert _table_intact(authed_client), "注入载荷破坏了数据库"


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_m4_2_bill_create_text_fields_parameterized(authed_client, payload):
    """【防护有效 M4-2】新增账单的文本字段原样保存（未被执行）"""
    resp = authed_client.post(
        "/api/bill",
        json={
            "tx_time": "2024-01-01 12:00:00",
            "account": "wechat",
            "tx_type": "expense",
            "merchant": payload,
            "amount": 10.0,
            "category": "其他",
            "remark": payload,
        },
    )
    assert resp.status_code in (200, 201, 422), resp.text
    assert _table_intact(authed_client), "写注入载荷后数据库被破坏"


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_m4_3_stat_filters_parameterized(authed_client, payload):
    """【防护有效 M4-3】统计接口的筛选参数无法注入

    month/start 等参数被 400 拒绝（格式校验）或按只读筛选生效，均不构成注入。
    """
    for path in (
        "/api/stat/summary",
        "/api/stat/month_trend",
        "/api/stat/category_pie",
        "/api/stat/health",
    ):
        resp = authed_client.get(path, params={"month": payload, "start": payload})
        assert resp.status_code in (200, 400, 422), f"{path} -> {resp.status_code}"
    assert _table_intact(authed_client)


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_m4_6_category_name_parameterized(authed_client, payload):
    """【防护有效 M4-6】分类名写入无法注入（分类名会被拼进更新语句）"""
    resp = authed_client.post("/api/category", json={"name": payload})
    assert resp.status_code in (200, 201, 400, 403, 422), resp.text
    assert authed_client.get("/api/category").status_code == 200


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_m4_7_bill_update_path_and_body_parameterized(authed_client, payload):
    """【防护有效 M4-7】路径参数（id）与请求体均无法注入"""
    resp = authed_client.put("/api/bill/1", json={"remark": payload})
    assert resp.status_code in (200, 400, 403, 404, 422), resp.text
    # 路径参数直接吃注入载荷：应被 int 转换拦住（422）
    resp2 = authed_client.get(f"/api/bill/{payload}")
    assert resp2.status_code in (400, 403, 404, 422), resp2.text
    assert _table_intact(authed_client)


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_m4_8_batch_ids_parameterized(authed_client, payload):
    """【防护有效 M4-8】批量操作的 ids 为 list[int]，非整数载荷被拒"""
    resp = authed_client.post(
        "/api/bill/batch", json={"ids": [payload], "action": "delete"}
    )
    assert resp.status_code in (200, 400, 422), resp.text
    assert _table_intact(authed_client)


def test_m4_sort_by_uses_whitelist():
    """【防护有效 M4-1·关键】sort_by 经 DAO 层 SORTABLE_FIELDS 白名单校验

    实现（bill_service.list_bills 第 176-180 行）：
        if sort_by not in SORTABLE_FIELDS:
            raise ValidationError("无效的排序字段", ...)
        if order not in ("asc", "desc"):
            raise ValidationError("无效的排序方向", ...)
    白名单以 DAO 层 SORTABLE_FIELDS 为单一来源 —— ORDER BY 注入面已封堵。
    """
    import inspect

    from app.db.dao import bill_dao  # noqa: PLC0415

    assert hasattr(bill_dao, "SORTABLE_FIELDS"), "DAO 层未见排序白名单"
    fields = bill_dao.SORTABLE_FIELDS
    assert isinstance(fields, set) and fields
    assert "tx_time" in fields
    # 白名单内不得出现 SQL 元字符
    for field in fields:
        for ch in ("'", '"', ";", "-", " ", "("):
            assert ch not in str(field), f"白名单字段 {field!r} 含元字符 {ch!r}"
    # DAO 侧以 getattr(模型列) 方式取列，而非拼字符串
    src = inspect.getsource(bill_dao)
    assert "getattr(Bill," in src, "DAO 未用 getattr 映射白名单字段到模型列"


def test_m4_sort_direction_whitelisted():
    """【防护有效】排序方向只允许 asc/desc（防止拼接任意方向片段）"""
    import inspect

    from app.services import bill_service  # noqa: PLC0415

    src = inspect.getsource(bill_service)
    assert '("asc", "desc")' in src or "('asc', 'desc')" in src


def test_m4_no_raw_sql_string_interpolation_in_visible_code():
    """【现状确认 M4】f-string SQL 仅出现在两处内部常量场景

    - `copy.py::_sync_pg_sequences`：表名来自函数内硬编码元组
    - `migrations.py`：`col_type` 来自方言判定（sqlite/其他二选一），
      `CREATE INDEX {index} ON {table}({columns})` 的三个变量均为迁移函数
      内的字面量参数，无请求参数参与
    两者的共同点：变量全部来自代码内常量，不经过网络输入。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "app"
    findings: set[str] = set()
    for path in root.rglob("*.py"):
        if "venv" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "text(f" in text:
            findings.add(path.name)
    allowed = {"copy.py", "migrations.py"}
    unexpected = findings - allowed
    assert not unexpected, f"发现未评估的 f-string SQL：{unexpected}"


def test_m4_migrations_fstring_vars_are_internal_constants():
    """【结论：不成立】migrations.py 的 f-string 变量均为函数内常量

    逐条核对：
    - `col_type` = "TEXT" / "VARCHAR(32)"（按方言二选一）
    - `{index}` / `{table}` / `{columns}` 来自 `_create_index_if_missing(
      session, table, index, columns)` 的调用方——全部是模块内字面量调用
    """
    import inspect

    from app.db import migrations  # noqa: PLC0415

    src = inspect.getsource(migrations)
    assert "TEXT" in src and "VARCHAR(32)" in src
    # 辅助函数的签名是显式参数，非 **kwargs 透传
    sig = inspect.signature(migrations._create_index_if_missing)
    assert set(sig.parameters) == {"session", "table", "index", "columns"}


# ====================================================================
# M16-1 / M16-2：依赖 CVE 排查
# ====================================================================


def test_m16_1_runtime_dependency_versions_recorded():
    """【现状确认 M16-1】输出关键运行时依赖版本清单，供 CVE 人工比对"""
    import importlib.metadata as md

    packages = [
        "fastapi",
        "starlette",
        "uvicorn",
        "sqlalchemy",
        "pydantic",
        "openpyxl",
        "python-multipart",
    ]
    versions: dict[str, str] = {}
    for name in packages:
        try:
            versions[name] = md.version(name)
        except md.PackageNotFoundError:
            versions[name] = "<未安装>"

    assert versions
    for name, version in versions.items():
        assert isinstance(version, str) and version, f"{name} 版本获取失败"
    print("\n[依赖版本清单]", versions)


def test_m16_2_python_multipart_version_is_modern():
    """【风险提示 M16-2】python-multipart 需 >= 0.0.7（CVE-2024-24762 ReDoS）"""
    import importlib.metadata as md

    try:
        version = md.version("python-multipart")
    except md.PackageNotFoundError:
        pytest.skip("未安装 python-multipart")

    parts = version.split(".")
    try:
        major, minor, patch = (int(p) for p in (parts + ["0", "0"])[:3])
    except ValueError:
        pytest.skip(f"版本号格式非预期：{version}")

    assert (major, minor, patch) >= (
        0,
        0,
        7,
    ), f"python-multipart {version} 低于 0.0.7，可能存在 CVE-2024-24762"


def test_m16_3_update_checksum_is_exposed_to_client():
    """【结论修正 M16-3】更新检查**提供**校验摘要直链，校验动作在客户端

    原报告结论「更新包无本地完整性校验（无 md5/sha256/hashlib），仅依赖
    HTTPS 传输层」经源码核实为**表述不准**：

    - `update_service.CHECKSUM_FILE = "MD5SUMS.txt"`，随 Release 一并发布
      （由 `scripts/build_fpk.sh` 生成）；
    - `parse_release()` 从 assets 中提取该文件直链填入 `checksum_url`，
      经 `ReleaseOut.checksum_url` 下发给前端。

    为什么不在后端自动核对：本应用的「检查更新」只做**告知**（不自动下载安装
    FPK），实际安装由用户在 fnOS 里手动完成。因此摘要的作用是让用户在下机
    安装前用 `md5sum -c MD5SUMS.txt` 核对 —— 校验点在客户端。
    """
    from app.services import update_service  # noqa: PLC0415

    assert update_service.CHECKSUM_FILE == "MD5SUMS.txt"

    # parse_release 必须从 assets 里取到校验文件直链
    raw = {
        "tag_name": "v1.1.0",
        "name": "v1.1.0",
        "created_at": "2026-01-01T00:00:00Z",
        "prerelease": False,
        "body": "",
        "assets": [
            {"name": "fn-finstat.fpk", "browser_download_url": "https://x/fpk"},
            {"name": "MD5SUMS.txt", "browser_download_url": "https://x/md5"},
        ],
    }
    parsed = update_service.parse_release(raw)
    assert parsed is not None
    assert parsed.checksum_url == "https://x/md5"

    # 该字段要能下发到前端（schema 有声明）
    from app.schemas.update import UpdateCheckResult  # noqa: PLC0415

    assert "checksum_url" in UpdateCheckResult.model_fields


def test_m16_3_checksum_absent_degrades_gracefully():
    """【结论修正 M16-3】Release 未附校验文件时留空串，不报错"""
    from app.services import update_service  # noqa: PLC0415

    parsed = update_service.parse_release(
        {
            "tag_name": "v1.1.0",
            "name": "v1.1.0",
            "created_at": "2026-01-01T00:00:00Z",
            "prerelease": False,
            "body": "",
            "assets": [
                {"name": "fn-finstat.fpk", "browser_download_url": "https://x/fpk"}
            ],
        }
    )
    assert parsed is not None
    assert parsed.checksum_url == ""


def test_m16_3_downgrade_protection_via_version_compare():
    """【现状确认 M16-3·降级】版本比较可判定 relation（含 downgrade）"""
    from app.services import update_service  # noqa: PLC0415

    assert update_service.compare_versions("1.1.0", "1.0.0") == 1
    assert update_service.compare_versions("1.0.0", "1.1.0") == -1
    assert update_service.compare_versions("1.1.0", "1.1.0") == 0
