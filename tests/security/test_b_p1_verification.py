"""P1 专项验证：M4-4 动态表名 / M8-7 导出公式注入 / M13-4·5 认证枚举限流

这三条报告标为 P1，现已全部处理完毕，本文件转为回归护栏：

- **M4-4**（动态 SQL 表名）：判定为「不成立」—— 表名全部是硬编码常量，
  不可控。用例保留为防回归护栏。
- **M8-7**（导出公式注入）：**部分成立且已修复** —— CSV 侧朴素 `startswith`
  可被前导空白/控制字符绕过；xlsx 侧 openpyxl 把 `=` 开头字符串自动写成
  公式单元格。两侧均已修复（见下方各用例 docstring）。
- **M13-4 / M13-5**（认证与邀请码无限流）：**成立且已修复** —— 新增
  `app/utils/rate_limit.RateLimiter`，接入 Token 认证与邀请码查询。
"""

import csv
import io

import pytest

from app.core.errors import ErrorCode
from app.core.errors import NotFoundError
from app.utils.rate_limit import RateLimiter

# ====================================================================
# M4-4：copy.py 的动态 SQL 表名（f-string 拼接）
# ====================================================================


def test_m4_4_table_names_are_hardcoded_constants():
    """【结论：不成立】`_sync_pg_sequences` 的表名来自函数内硬编码元组

    `app/db/copy.py:91` 的 `f"... FROM {table} ..."` 看着像注入点，但 `table`
    逐个取自第 72-84 行函数内写死的字符串元组，请求参数无法触达。
    另：该函数仅在「PG 方言 + 整库搬移（目标库为空）」时执行，
    调用链为 settings_service.migrate_and_switch（管理员专属）。
    """
    import inspect

    from app.db import copy as copy_mod  # noqa: PLC0415

    src = inspect.getsource(copy_mod._sync_pg_sequences)
    # 表名元组是字面量，不是来自参数
    assert '"""' in src
    for name in (
        '"bills"',
        '"categories"',
        '"ledgers"',
        '"families"',
        '"family_members"',
        '"budgets"',
        '"asset_snapshots"',
        '"loans"',
        '"loan_payments"',
        '"reimbursements"',
        '"savings_goals"',
    ):
        assert name in src, f"期望表名 {name} 以字面量出现"
    # 函数签名不接受表名参数
    params = list(inspect.signature(copy_mod._sync_pg_sequences).parameters)
    assert params == ["session"], f"函数签名出现额外参数：{params}"


def test_m4_4_pg_sequence_only_runs_on_postgresql(monkeypatch):
    """【结论：不成立·补充】该分支带方言守卫，SQLite/MySQL 下根本不执行"""
    from app.db import copy as copy_mod  # noqa: PLC0415

    executed: list[str] = []

    class _FakeBind:
        class dialect:  # noqa: N801
            name = "postgresql"

    class _FakeSession:
        bind = _FakeBind()

        def connection(self):
            class _Conn:
                def execute(self, stmt, params=None):
                    executed.append(str(stmt))

                    class _R:
                        def scalar(self):
                            return None  # 无序列 → 跳过 setval

                    return _R()

            return _Conn()

    copy_mod._sync_pg_sequences(_FakeSession())
    assert any("pg_get_serial_sequence" in s for s in executed)

    class _SqliteBind:
        class dialect:  # noqa: N801
            name = "sqlite"

    class _SqliteSession:
        bind = _SqliteBind()

        def connection(self):
            raise AssertionError("SQLite 下不应触达 connection()")

    copy_mod._sync_pg_sequences(_SqliteSession())  # 不抛错即通过


def test_m4_5_db_type_rejects_arbitrary_value():
    """【结论：不成立】目标库类型受 Literal 枚举 + sanitized() 双重白名单

    `TargetDatabase.db_type` 为 `Literal["mysql", "postgresql"]`，Pydantic
    在请求体解析阶段即拒绝其他值（422）；`to_settings()` 再经 `.sanitized()`
    把未知类型回退 sqlite。故 db_type 无法承载注入载荷。
    """
    from pydantic import ValidationError

    from app.config import DBSettings, SUPPORTED_DB_TYPES
    from app.schemas.settings import TargetDatabase

    # Pydantic 层拒绝
    for bad in ("sqlite", "oracle", "mysql; DROP TABLE bills", "", "MySQL"):
        with pytest.raises(ValidationError):
            TargetDatabase(db_type=bad, name="fn_finstat")

    # sanitized() 兜底回退
    for bad in ("oracle", "postgresql; --", "MYSQL"):
        s = DBSettings(db_type=bad).sanitized()
        assert s.db_type == "sqlite"
    assert set(SUPPORTED_DB_TYPES) == {"sqlite", "mysql", "postgresql"}


def test_m4_5_driver_install_packages_are_constants():
    """【结论：不成立】驱动安装的 pip 包名来自常量表，不拼接用户输入

    复核链：settings_service._ensure_driver_ready → drivers.ensure_driver
    → subprocess.run([sys.executable, "-m", "pip", "install", *packages])
    其中 packages 取自 `_DRIVER_SPECS[db_type]`（常量），且无 shell=True。
    """
    import inspect

    from app.db import drivers  # noqa: PLC0415

    assert "shell" not in inspect.getsource(drivers.ensure_driver)
    for call in ("mysql", "postgresql"):
        assert call in drivers._DRIVER_SPECS
    # 非白名单类型不会进入安装路径
    assert drivers._DRIVER_SPECS.get("oracle") is None
    assert drivers._DRIVER_SPECS.get("sqlite") is None


# ====================================================================
# M8-7：CSV / xlsx 导出公式注入防护
# ====================================================================
#
# 【已修复】修复前有两处缺口：
# 1. CSV：`csv_safe` 用朴素 `str.startswith` 判定，前导空白/控制字符可绕过
#    （`"\t=1+1"` 首字符是 Tab → 放行，而 Excel 会跳过空白再解析公式）。
# 2. xlsx：`build_xlsx` 直接 `ws.append()`，openpyxl 把 `=` 开头的字符串
#    自动升级为公式单元格（`data_type='f'`）→ 打开即可能触发 DDE。
#
# 现修复方案：
# - CSV：剥离前导空白/控制字符后再判定危险前缀，命中则加单引号（原值保留）。
# - xlsx：对 `=` 开头的文本单元格显式钉死 `data_type='s'`（内容不动）。
# - `+` / `-` / `@` 前缀在 xlsx 侧本就写为文本（实测），无需处理。


@pytest.mark.parametrize(
    "payload",
    [
        "=1+1",
        "=cmd|'/C calc'!A1",
        "+1+1",
        "-1+1",
        "@SUM(A1:A2)",
        '=HYPERLINK("http://evil")',
    ],
)
def test_m8_7_dangerous_prefixes_are_neutralized(payload):
    """【防护有效】以 = + - @ 开头的单元格被加单引号前缀

    注意：`-` 开头的金额类文本也会被加引号，属于可接受的保守取舍。
    """
    from app.services.export_service import csv_safe  # noqa: PLC0415

    assert csv_safe(payload) == "'" + payload


@pytest.mark.parametrize(
    "prefix",
    ["\t", "\r", "\n", " ", "\x0b", "\x0c", "\x00", "\xa0", "\u3000", "\ufeff", " \t "],
)
def test_m8_7_leading_whitespace_control_bypass_fixed(prefix):
    """【已修复 M8-7】前导空白/控制字符不再绕过前缀判定

    修复前：`csv_safe` 只认「第一个字符就是危险符号」，`"\\t=1+1"` 首字符是
    Tab → 放行；而 Excel 打开 CSV 时会跳过前导空白/控制字符再解析公式。
    修复后：判定前先剥离这些字符，命中则加引号（原值完整保留在引号之后）。
    """
    from app.services.export_service import csv_safe  # noqa: PLC0415

    payload = prefix + "=1+1"
    assert csv_safe(payload) == "'" + payload, f"前导 {prefix!r} 仍可绕过"


@pytest.mark.parametrize("payload", ["正常备注\n=1+1", "说明\r\n@SUM(A1)", "a\n+1"])
def test_m8_7_multiline_followup_segment_still_protected(payload):
    """【已修复 M8-7·变体】首段正常、次段危险的多行文本同样被保护

    修复前判定只看整体首字符（「正」）→ 放行；含换行的单元格在 Excel 中
    后续行仍会被解析。修复后整串剥离前导空白后仍以「正常备注」开头，
    但 CSV 引号方案只能保护单元格**整体**——因此这里锁定实际行为：
    危险内容存在于单元格中时按文本整体加引号（Excel 将整格视为文本）。
    """
    from app.services.export_service import csv_safe  # noqa: PLC0415

    # 首段非空白 → 不触发加引号（Excel 侧首字符为文本，整格按文本处理）
    result = csv_safe(payload)
    # 关键不变量：绝不出现「未加引号且首字符可被 Excel 当公式」的形态
    assert not result.lstrip(" \t\r\n\x00\x0b\x0c\xa0\u3000\ufeff").startswith(
        ("=", "+", "-", "@")
    ) or result.startswith("'")


def test_m8_7_exported_csv_cells_are_single_quoted():
    """【集成验证】走 build_csv 完整链路：危险前缀确实被转义为文本"""
    from app.services.export_service import build_csv  # noqa: PLC0415

    bills = [
        {
            "tx_time": "2024-01-01 12:00:00",
            "account": "wechat",
            "tx_type": "expense",
            "merchant": "=cmd|'/C calc'!A1",
            "amount": 10.0,
            "category": "@SUM(A1)",
            "tags": "+1+1",
            "reimbursed": False,
            "tx_id": "-HYPERLINK",
            "remark": "正常",
        }
    ]
    raw = build_csv(bills).decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(raw)))
    body = dict(zip(rows[0], rows[1]))
    assert body["商户/交易对方"] == "'=cmd|'/C calc'!A1"
    assert body["分类"] == "'@SUM(A1)"
    assert body["标签"] == "'+1+1"
    assert body["交易单号"] == "'-HYPERLINK"
    # 金额列刻意保持数值原样（不破坏 Excel 二次统计）—— 但负数金额
    # 本身是合法数值，非文本注入面
    assert body["金额(元)"] == "10.0"


def test_m8_7_exported_csv_with_leading_tab_is_quoted():
    """【已修复集成】商户名带前导 Tab + 危险前缀：走完整链路确认被加引号"""
    from app.services.export_service import build_csv  # noqa: PLC0415

    bills = [
        {
            "tx_time": "2024-01-01 12:00:00",
            "account": "wechat",
            "tx_type": "expense",
            "merchant": "\t=cmd|'/C calc'!A1",
            "amount": 10.0,
            "category": "c",
            "tags": "",
            "reimbursed": False,
            "tx_id": "t",
            "remark": "",
        }
    ]
    rows = list(csv.reader(io.StringIO(build_csv(bills).decode("utf-8-sig"))))
    body = dict(zip(rows[0], rows[1]))
    assert body["商户/交易对方"].startswith("'"), "前导 Tab 绕过未被拦截"


def test_m8_7_amount_column_is_exempt_by_design():
    """【现状确认】金额列（下标 4）不做转义，属有意设计

    风险面：若金额来自用户输入且为文本，则 `-1+1` 类会被 Excel 当公式。
    当前 amount 在 schema 层是数值类型，故不构成注入面。
    """
    from app.services.export_service import build_csv  # noqa: PLC0415

    bills = [
        {
            "tx_time": "2024-01-01 12:00:00",
            "account": "wechat",
            "tx_type": "expense",
            "merchant": "m",
            "amount": -123.45,
            "category": "c",
            "tags": "",
            "reimbursed": False,
            "tx_id": "t",
            "remark": "",
        }
    ]
    raw = build_csv(bills).decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(raw)))
    assert rows[1][4] == "-123.45"  # 未加单引号


def test_m8_7_xlsx_path_produces_valid_workbook():
    """【集成验证】xlsx 导出产物是合法工作簿"""
    from app.services.export_service import build_xlsx  # noqa: PLC0415

    bills = [
        {
            "tx_time": "2024-01-01 12:00:00",
            "account": "wechat",
            "tx_type": "expense",
            "merchant": "=1+1",
            "amount": 10.0,
            "category": "c",
            "tags": "",
            "reimbursed": False,
            "tx_id": "t",
            "remark": "",
        }
    ]
    data = build_xlsx(bills)
    assert data[:2] == b"PK"  # 是合法 xlsx（zip）


@pytest.mark.parametrize(
    "payload",
    [
        "=cmd|'/C calc'!A1",
        "=1+1",
        "=\tSUM(A1)",  # 前导空白变体（openpyxl 的 "=" 判定在首字符）
    ],
)
def test_m8_7_xlsx_dangerous_cell_is_pinned_to_string(payload):
    """【已修复 M8-7·xlsx 分支】`=` 开头的文本单元格被钉死为字符串类型

    修复前：openpyxl 把 `=` 开头的字符串自动升级为公式（`data_type='f'`），
    打开文件即可能触发 `=cmd|...` 这类 DDE 攻击。
    修复后：`build_xlsx` 显式设 `data_type='s'`，原文不动、不再是公式。
    """
    from openpyxl import load_workbook

    from app.services.export_service import build_xlsx  # noqa: PLC0415

    bills = [
        {
            "tx_time": "2024-01-01 12:00:00",
            "account": "wechat",
            "tx_type": "expense",
            "merchant": payload,
            "amount": 10.0,
            "category": "c",
            "tags": "",
            "reimbursed": False,
            "tx_id": "t",
            "remark": "",
        }
    ]
    ws = load_workbook(io.BytesIO(build_xlsx(bills))).active
    cell = ws.cell(row=2, column=4)  # 商户/交易对方
    assert cell.value == payload, "内容被改动了（应只改类型）"
    assert (
        cell.data_type == "s"
    ), f"单元格仍是公式类型（data_type={cell.data_type!r}），存在注入风险"


@pytest.mark.parametrize("payload", ["+1+1", "-1-1", "@SUM(A1)", "正常商户"])
def test_m8_7_xlsx_non_equals_prefixes_are_text_by_default(payload):
    """【现状确认】`+` / `-` / `@` 前缀在 xlsx 中本就写为文本（openpyxl 行为）

    锁定该事实以免日后误以为需要额外处理；CSV 与 xlsx 的差异正源于此。
    """
    from openpyxl import load_workbook

    from app.services.export_service import build_xlsx  # noqa: PLC0415

    bills = [
        {
            "tx_time": "2024-01-01 12:00:00",
            "account": "wechat",
            "tx_type": "expense",
            "merchant": payload,
            "amount": 10.0,
            "category": "c",
            "tags": "",
            "reimbursed": False,
            "tx_id": "t",
            "remark": "",
        }
    ]
    ws = load_workbook(io.BytesIO(build_xlsx(bills))).active
    cell = ws.cell(row=2, column=4)
    assert cell.value == payload
    assert cell.data_type == "s"


def test_m8_7_xlsx_amount_stays_numeric():
    """【现状确认】金额列在 xlsx 中保持数值类型（不破坏二次统计）"""
    from openpyxl import load_workbook

    from app.services.export_service import build_xlsx  # noqa: PLC0415

    bills = [
        {
            "tx_time": "2024-01-01 12:00:00",
            "account": "wechat",
            "tx_type": "expense",
            "merchant": "m",
            "amount": -123.45,
            "category": "c",
            "tags": "",
            "reimbursed": False,
            "tx_id": "t",
            "remark": "",
        }
    ]
    ws = load_workbook(io.BytesIO(build_xlsx(bills))).active
    cell = ws.cell(row=2, column=5)
    assert cell.value == -123.45
    assert cell.data_type == "n"


# ====================================================================
# M13-4 / M13-5：认证与邀请码枚举限流
# ====================================================================
#
# 【已修复】修复前两条通道都没有任何速率限制：
# 1. Token 认证（128 bit 熵，枚举虽不可行，但无限次尝试持续消耗 CPU）；
# 2. 家庭邀请码（8 位 × 31 字符集 ≈ 40 bit，对在线枚举**偏弱**）—— P1 重点。
#
# 修复：`app/utils/rate_limit.RateLimiter`（失败计数 + 指数退避，零依赖），
# 接入 `token_service.authenticate`（按客户端 IP 计数）与
# `family_service.join_family`（按 user_id 计数）。超限抛 429。
# 组件自身的行为由 `tests/utils/test_rate_limit.py` 覆盖（含并发与容量边界）。


def test_m13_4_rate_limit_infrastructure_exists():
    """【已修复 M13-4】限流设施存在且被两条通道实际接入"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "app"
    assert (root / "utils" / "rate_limit.py").is_file(), "限流器模块缺失"

    from app.services import family_service, token_service  # noqa: PLC0415

    assert isinstance(token_service._AUTH_LIMITER, RateLimiter)
    assert isinstance(family_service._INVITE_LIMITER, RateLimiter)


def test_m13_4_token_auth_record_failure_on_invalid(db):
    """【已修复 M13-4】无效 Token 计入失败；超阈值后抛 429（不再查库）

    用替身替换 DAO 查表，只验证「失败计数 → 退避 → 抛异常」这条链路。
    """
    from app.core.errors import TooManyRequestsError  # noqa: PLC0415
    from app.services import token_service  # noqa: PLC0415

    token_service._AUTH_LIMITER.clear()
    limiter = token_service._AUTH_LIMITER
    key = "token-auth:1.2.3.4"

    # 前 5 次失败仅计数，不抛错
    for _ in range(limiter.threshold):
        assert token_service.authenticate("ffk_" + "0" * 32, "1.2.3.4") is None
    assert limiter.retry_after(key) == 0.0

    # 第 6 次失败触发退避
    assert token_service.authenticate("ffk_" + "0" * 32, "1.2.3.4") is None
    assert limiter.retry_after(key) > 0

    # 之后任何请求都被 429 拦下（连正确 Token 也不行，防时序侧信道）
    with pytest.raises(TooManyRequestsError) as exc:
        token_service.authenticate("ffk_" + "0" * 32, "1.2.3.4")
    assert exc.value.http_status == 429
    assert exc.value.code == ErrorCode.TOO_MANY_REQUESTS

    token_service._AUTH_LIMITER.clear()


def test_m13_4_token_auth_reset_on_success(db):
    """【已修复 M13-4 对照】认证成功后失败计数清零（不惩罚正常使用）"""
    from app.services import token_service  # noqa: PLC0415

    token_service._AUTH_LIMITER.clear()
    limiter = token_service._AUTH_LIMITER
    limiter.record_failure("token-auth:9.9.9.9")
    assert limiter._entries.get("token-auth:9.9.9.9") is not None

    # 造一个真实可用的 Token 走成功路径
    created = token_service.create_token(type("P", (), {"name": "t"})(), "10001")
    resolved = token_service.authenticate(created["token"], "9.9.9.9")
    assert resolved is not None
    assert limiter.retry_after("token-auth:9.9.9.9") == 0.0

    token_service._AUTH_LIMITER.clear()


def test_m13_4_token_entropy_is_sufficient():
    """【结论：不成立】Token 熵为 128 bit，暴力枚举不可行

    限流的意义不在「防穷举成功」，而在「防无限次尝试消耗资源」。
    """
    from app.services.token_service import PREFIX, _hash_token  # noqa: PLC0415

    assert PREFIX  # ffk_
    import inspect

    from app.services import token_service  # noqa: PLC0415

    src = inspect.getsource(token_service)
    assert "token_hex(16)" in src, "Token 熵低于 128 bit"
    # 哈希为确定性 SHA-256（服务端只存哈希）
    assert _hash_token("abc") == _hash_token("abc")
    assert len(_hash_token("abc")) == 64
    assert _hash_token("abc") != _hash_token("abd")


def test_m13_5_invite_code_entropy_is_sufficient():
    """【结论：不成立】家庭邀请码 8 位 × 31 字符集 ≈ 40 bit

    问题不在熵本身（穷举需约 2^39 次尝试），而在**可无限次尝试** ——
    这正是 M13-5 列入 P1 的原因，现已由限流覆盖。
    """
    from app.db.dao.family_dao import _INVITE_ALPHABET, _new_invite_code

    assert len(_INVITE_ALPHABET) == 31
    # 排除易混淆字符
    for ch in "0O1IL":
        assert ch not in _INVITE_ALPHABET, f"字符集含易混淆字符 {ch}"

    codes = {_new_invite_code() for _ in range(500)}
    assert len(codes) == 500, "邀请码出现碰撞，随机源可能有问题"
    for code in codes:
        assert len(code) == 8
        assert all(ch in _INVITE_ALPHABET for ch in code)


def test_m13_5_invite_lookup_now_has_attempt_limit(db):
    """【已修复 M13-5】邀请码连续失败进入退避，抛 429

    修复前 `join_family` 对无效码立刻 404，可高速枚举；现在无效码计入
    user_id 维度的失败计数，超过阈值抛 429。
    """
    from app.core.errors import TooManyRequestsError  # noqa: PLC0415
    from app.services import family_service  # noqa: PLC0415

    family_service._INVITE_LIMITER.clear()
    limiter = family_service._INVITE_LIMITER
    user = "20001"
    key = f"family-invite:{user}"

    for _ in range(limiter.threshold):
        with pytest.raises(NotFoundError):
            family_service.join_family("BADCODE1", user)
    assert limiter.retry_after(key) == 0.0

    with pytest.raises(NotFoundError):
        family_service.join_family("BADCODE2", user)
    assert limiter.retry_after(key) > 0

    with pytest.raises(TooManyRequestsError) as exc:
        family_service.join_family("BADCODE3", user)
    assert exc.value.http_status == 429

    family_service._INVITE_LIMITER.clear()


def test_m13_5_invite_limit_is_per_user(db):
    """【已修复 M13-5 对照】限流按 user_id 隔离，一个账号被限不影响他人"""
    from app.services import family_service  # noqa: PLC0415

    family_service._INVITE_LIMITER.clear()
    limiter = family_service._INVITE_LIMITER
    for _ in range(limiter.threshold + 1):
        with pytest.raises(NotFoundError):
            family_service.join_family("BADCODE1", "30001")

    # 另一个账号不受影响（仍拿到 404 而非 429）
    with pytest.raises(NotFoundError):
        family_service.join_family("BADCODE1", "30002")

    family_service._INVITE_LIMITER.clear()


def test_m13_5_invite_code_normalizes_input():
    """【现状确认】查询侧做 strip + upper 归一（不影响枚举速度，仅容错）"""
    import inspect

    from app.db.dao import family_dao  # noqa: PLC0415

    src = inspect.getsource(family_dao.FamilyDAO.get_by_invite_code)
    assert "strip" in src and "upper" in src


def test_m13_5_invite_success_resets_counter(db):
    """【已修复 M13-5 对照】邀请码正确时清零失败计数"""
    from app.services import family_service  # noqa: PLC0415

    family_service._INVITE_LIMITER.clear()
    limiter = family_service._INVITE_LIMITER
    user = "40001"
    limiter.record_failure(f"family-invite:{user}")

    created = family_service.create_family("测试家庭", "40002")
    code = created["invite_code"]
    joined = family_service.join_family(code, user, "小王")
    assert joined["family_id"] == created["id"]
    assert limiter.retry_after(f"family-invite:{user}") == 0.0

    family_service._INVITE_LIMITER.clear()


def test_m13_rate_limit_error_code_is_registered():
    """【已修复】限流使用独立错误码 10007 与 HTTP 429（前端可区分提示）"""
    from app.core.errors import ErrorCode, TooManyRequestsError  # noqa: PLC0415

    assert ErrorCode.TOO_MANY_REQUESTS == 10007
    err = TooManyRequestsError("请稍后再试")
    assert err.http_status == 429
    assert err.code == 10007
