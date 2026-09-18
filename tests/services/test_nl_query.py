"""自然语言查账（T-6.1）测试：规则表驱动 + LLM mock + 降级/配额 + 越权隔离 + DAO

验收口径对应（开发计划 T-6.1 / REQ-QRY-001/003/006）：
- 典型问法意图命中率（20 问表驱动，规则路径占比 ≥ 60%）
- 模型输出白名单校验（非法枚举/分类/字段一律拒绝或丢弃）
- 越权用例全部被拒（user_id 服务端注入，接口不接受身份参数）
- 模型不可用降级规则路径并明确告知
"""

import json
from datetime import date

import pytest

from app.config import AISettings
from app.db.dao.bill_dao import BillDAO
from app.db.dao.category_dao import CategoryDAO
from app.services import nl_query as nl_service
from tests.conftest import USER_A, USER_B

TODAY = date(2026, 9, 17)
A_HEADERS = {"X-Trim-Userid": USER_A}

# 每例只断言给出的键；categories 用集合比较（规则按分类名长度优先抽取，顺序不敏感）
_SPEC_GETTERS = {
    "time_start": lambda s: s["time"]["start"],
    "time_end": lambda s: s["time"]["end"],
    "time_label": lambda s: s["time"]["label"],
    "categories": lambda s: set(s["categories"]),
    "merchants": lambda s: s["merchants"],
    "metric": lambda s: s["metric"],
    "group_by": lambda s: s["group_by"],
    "order_by": lambda s: s["order_by"],
    "limit": lambda s: s["limit"],
}

# 20 条典型问法（时间锚定 TODAY=2026-09-17）：rule=规则路径必须命中，llm=必须让位给模型
TYPICAL_CASES = [
    (
        "上个月奶茶花了多少",
        "rule",
        {"time_start": "2026-08-01", "time_end": "2026-08-31", "merchants": ["奶茶"]},
    ),
    ("本月餐饮支出", "rule", {"time_label": "2026 年 9 月", "categories": {"餐饮"}}),
    ("今年收入多少", "rule", {"time_label": "2026 年度", "metric": "income"}),
    (
        "去年12月交通费花了多少",
        "rule",
        {"time_start": "2025-12-01", "time_end": "2025-12-31", "categories": {"交通"}},
    ),
    ("这个月各分类支出", "rule", {"group_by": "category"}),
    ("上个月商户排行", "rule", {"group_by": "merchant", "order_by": "amount_desc"}),
    ("最近7天花了多少", "rule", {"time_start": "2026-09-11", "time_end": "2026-09-17"}),
    ("本周咖啡消费", "rule", {"time_label": "本周", "merchants": ["咖啡"]}),
    (
        "2026-08-01到2026-08-15支出了多少",
        "rule",
        {"time_start": "2026-08-01", "time_end": "2026-08-15"},
    ),
    (
        "8月15日花了多少钱",
        "rule",
        {"time_start": "2026-08-15", "time_end": "2026-08-15"},
    ),
    ("2025年支出多少", "rule", {"time_label": "2025 年度"}),
    (
        "上上个月买菜花了多少钱",
        "rule",
        {"time_label": "2026 年 7 月", "merchants": ["买菜"]},
    ),
    (
        "近30天餐饮和交通各花了多少",
        "rule",
        {"categories": {"餐饮", "交通"}, "metric": "expense"},
    ),
    (
        "上季度充值花了多少",
        "rule",
        {"time_label": "2026 年 Q2 季度", "merchants": ["充值"]},
    ),
    ("上个月在瑞幸咖啡花了多少钱", "rule", {"merchants": ["瑞幸咖啡"]}),
    ("本月网购花了多少钱", "rule", {"merchants": ["网购"]}),
    ("上个月转账有多少笔", "rule", {"metric": "count"}),
    ("上个月最喜欢在哪家店消费", "rule", {"group_by": "merchant"}),
    ("本月和上个月哪个月花得多", "llm", {}),
    ("吃的方面花了多少", "llm", {}),
]


def _category_set() -> set[str]:
    return {c["name"] for c in CategoryDAO.list_all()}


def _seed(rows, user_id=USER_A, prefix="NLQ"):
    """按 (tx_time, tx_type, amount, category, merchant) 批量造流水"""
    records = [
        {
            "tx_time": tx_time,
            "account": "wechat",
            "tx_type": tx_type,
            "merchant": merchant,
            "amount": amount,
            "category": category,
            "tx_id": f"{prefix}-{i:04d}",
            "remark": "",
        }
        for i, (tx_time, tx_type, amount, category, merchant) in enumerate(rows)
    ]
    return BillDAO.insert_many(records, user_id)


@pytest.mark.parametrize(
    "question,expected_path,expected",
    TYPICAL_CASES,
    ids=[c[0] for c in TYPICAL_CASES],
)
def test_typical_questions(db, question, expected_path, expected):
    """典型问法表驱动：意图解析正确 + 路由符合预期

    规则命中 18/20 = 90% ≥ 60%（验收：规则命中占比 ≥ 60%）。
    """
    spec, hit = nl_service._rule_parse(question, TODAY, _category_set())
    assert hit if expected_path == "rule" else not hit, f"路由错误：{spec}"
    for key, want in expected.items():
        got = _SPEC_GETTERS[key](spec)
        assert got == want, f"{question} 字段 {key}: 期望 {want!r}, 实际 {got!r}"


def test_rule_hit_never_calls_llm(db, monkeypatch):
    """规则命中的问法绝不发起 LLM 调用（零成本红线）"""

    def _boom(*args, **kwargs):
        raise AssertionError("规则命中不应调用 LLM")

    monkeypatch.setattr(nl_service, "chat", _boom)
    spec, hit = nl_service._rule_parse("上个月奶茶花了多少", TODAY, _category_set())
    assert hit


# ---- LLM 兜底：白名单校验 ----


def _install_llm(monkeypatch, payload, calls):
    """AI 配置置为 ready 并 mock chat 返回 payload；记录请求消息；重置配额计数"""

    def fake_chat(settings, messages, max_tokens, timeout=60):
        calls.append(
            {"messages": messages, "max_tokens": max_tokens, "timeout": timeout}
        )
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(nl_service, "load_ai_settings", lambda: AISettings(api_key="k"))
    monkeypatch.setattr(nl_service, "chat", fake_chat)
    # 配额计数是模块级状态，逐测试隔离
    monkeypatch.setattr(nl_service, "_quota_used", {})


def test_llm_output_whitelist(db, monkeypatch):
    """模型输出：非法分类丢弃、非白名单字段忽略、字段钳制、枚举兜底"""
    calls: list = []
    _install_llm(
        monkeypatch,
        {
            "time": {"start": "2026-08-31", "end": "2026-08-01", "label": "8月"},
            "categories": ["餐饮", "不存在的分类", "餐饮"],
            "merchants": ["  奶茶  ", "", "x" * 100],
            "metric": "支出",  # 非法枚举 → expense
            "group_by": "category",
            "order_by": "key_asc",
            "limit": 999,  # 钳制到 100
            "user_id": USER_B,  # 越权/非白名单字段：必须被忽略
            "sql": "DELETE FROM bills",  # 注入企图：必须被忽略
        },
        calls,
    )
    result = nl_service.query(USER_A, "吃的方面花了多少", today=TODAY)
    assert result["source"] == "llm"
    assert len(calls) == 1
    # prompt 携带真实分类表与今天日期，超时为交互路径专用短超时
    system = calls[0]["messages"][0]["content"]
    assert "餐饮" in system and "2026-09-17" in system
    assert calls[0]["timeout"] == nl_service.LLM_TIMEOUT
    spec = result["spec"]
    assert spec["categories"] == ["餐饮"]  # 非法分类被丢弃、重复被去重
    assert spec["merchants"][0] == "奶茶"  # 空白归一
    assert all(len(m) <= 64 for m in spec["merchants"])
    assert spec["metric"] == "expense"
    assert spec["group_by"] == "category"
    assert spec["order_by"] == "key_asc"
    assert spec["limit"] == 100
    assert set(spec) == {
        "time",
        "categories",
        "merchants",
        "metric",
        "group_by",
        "order_by",
        "limit",
    }  # user_id / sql 等一律进不了查询对象


def test_llm_garbage_output_degrades(db, monkeypatch):
    """模型输出不是 JSON → 降级规则路径并明确告知"""
    monkeypatch.setattr(nl_service, "load_ai_settings", lambda: AISettings(api_key="k"))
    monkeypatch.setattr(nl_service, "chat", lambda *a, **k: "这不是JSON")
    result = nl_service.query(USER_A, "吃的方面花了多少", today=TODAY)
    assert result["source"] == "fallback"
    assert result["degraded"] is True
    assert "规则模式" in result["message"]


def test_llm_failure_degrades(db, monkeypatch):
    """模型调用失败 → 降级规则路径并明确告知（REQ-QRY-006）"""
    from app.services.ai_service import AIClientError

    monkeypatch.setattr(nl_service, "load_ai_settings", lambda: AISettings(api_key="k"))

    def _fail(*args, **kwargs):
        raise AIClientError("网络不可达")

    monkeypatch.setattr(nl_service, "chat", _fail)
    result = nl_service.query(USER_A, "吃的方面花了多少", today=TODAY)
    assert result["source"] == "fallback"
    assert result["degraded"] is True
    assert "解析失败" in result["message"]
    # 降级后仍用规则口径执行（全部时间支出汇总），不是空响应
    assert result["count"] == 0
    assert "没有查询到流水" in result["answer"]


def test_llm_not_configured_degrades(db):
    """未配置 AI → 直接规则路径 + 提示（不发起调用）"""
    result = nl_service.query(USER_A, "吃的方面花了多少", today=TODAY)
    assert result["source"] == "fallback"
    assert "未配置" in result["message"]


def test_llm_daily_quota(db, monkeypatch):
    """超出单日调用上限 → 规则路径接管并提示（v0.6 出口标准）"""
    monkeypatch.setattr(nl_service, "LLM_DAILY_LIMIT", 2)
    monkeypatch.setattr(nl_service, "load_ai_settings", lambda: AISettings(api_key="k"))
    calls: list = []
    _install_llm(monkeypatch, {}, calls)
    # 已有 2 次当日记录 → 配额用尽
    nl_service._record_llm_call(TODAY)
    nl_service._record_llm_call(TODAY)
    result = nl_service.query(USER_A, "吃的方面花了多少", today=TODAY)
    assert result["source"] == "fallback"
    assert "上限" in result["message"]
    assert calls == []


def test_llm_quota_counts_only_llm_calls(db, monkeypatch):
    """配额只统计真实 LLM 调用：规则命中不计数"""
    calls: list = []
    _install_llm(monkeypatch, {"metric": "expense"}, calls)
    nl_service.query(USER_A, "上个月奶茶花了多少", today=TODAY)  # 规则命中
    assert calls == []
    assert nl_service._llm_quota_left(TODAY) == nl_service.LLM_DAILY_LIMIT


# ---- 查询执行：隔离、口径与确定性答案 ----


def test_query_scoped_and_answer(db):
    """查询按账号隔离；答案/口径/覆盖笔数来自数据库而非模型"""
    _seed(
        [
            ("2026-08-05 12:00:00", "expense", 20.0, "餐饮", "奶茶店"),
            ("2026-08-06 12:00:00", "expense", 15.5, "餐饮", "奶茶店"),
            ("2026-08-07 12:00:00", "income", 300.0, "其他", "工资"),
        ],
        USER_A,
        "NLA",
    )
    other = _seed(
        [("2026-08-05 12:00:00", "expense", 999.0, "餐饮", "奶茶店")], USER_B, "NLB"
    )
    assert other == 1
    result = nl_service.query(USER_A, "2026-08-01到2026-08-31奶茶花了多少", today=TODAY)
    assert result["source"] == "rule"
    assert result["total"] == 35.5  # USER_B 的 999 不参与
    assert result["count"] == 2
    assert result["spec"]["time"]["start"] == "2026-08-01"
    assert result["spec"]["merchants"] == ["奶茶"]
    assert "35.50" in result["answer"] and "2 笔" in result["answer"]
    assert result["details"][0]["merchant"] == "奶茶店"


def test_query_grouped_and_empty(db):
    """分组查询返回组行与总量；无数据显示「没有查询到」不编造"""
    _seed(
        [
            ("2026-08-05 12:00:00", "expense", 20.0, "餐饮", "A店"),
            ("2026-08-06 12:00:00", "expense", 35.0, "交通", "B店"),
            ("2026-08-07 12:00:00", "expense", 10.0, "餐饮", "C店"),
        ],
        USER_A,
        "NLG",
    )
    result = nl_service.query(USER_A, "2026年8月各分类支出", today=TODAY)
    assert result["count"] == 3
    assert result["total"] == 65.0  # 总量是全部匹配流水的合计，不是首组
    assert result["grouped"] == [
        {"key": "交通", "amount": 35.0, "count": 1},
        {"key": "餐饮", "amount": 30.0, "count": 2},
    ]
    assert "按分类统计" in result["answer"]

    empty = nl_service.query(USER_A, "2025年1月各分类支出", today=TODAY)
    assert empty["count"] == 0
    assert empty["grouped"] == []
    assert "没有查询到流水" in empty["answer"]


def test_query_group_truncation_flag(db):
    """分组数超 limit 时 truncated=True 且组数被截断"""
    rows = [
        (f"2026-08-{d:02d} 12:00:00", "expense", float(d), "餐饮", f"店{d}")
        for d in range(1, 6)
    ]
    _seed(rows, USER_A, "NLT")
    result = nl_service.query(USER_A, "2026年8月商户TOP2", today=TODAY)
    assert result["truncated"] is True
    assert len(result["grouped"]) == 2
    assert result["count"] == 5


def test_question_too_long_rejected(db):
    """超长问题在服务层拒绝（schema 层另有 200 字上限）"""
    from app.core.errors import ValidationError

    with pytest.raises(ValidationError):
        nl_service.query(USER_A, "花" * 201, today=TODAY)
    with pytest.raises(ValidationError):
        nl_service.query(USER_A, "   ", today=TODAY)


# ---- API 层：越权与入参白名单（REQ-QRY-003）----


def test_api_query_ok_and_isolation(client):
    _seed(
        [("2026-08-05 12:00:00", "expense", 12.0, "餐饮", "奶茶店")],
        USER_A,
        "APIA",
    )
    _seed(
        [("2026-08-05 12:00:00", "expense", 88.0, "餐饮", "奶茶店")],
        USER_B,
        "APIB",
    )
    resp = client.post(
        "/api/nl-query",
        json={"question": "2026-08-01到2026-08-31奶茶花了多少"},
        headers=A_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 12.0  # 只看得到自己的流水
    assert data["question"] == "2026-08-01到2026-08-31奶茶花了多少"
    assert data["spec"]["merchants"] == ["奶茶"]
    assert {d["merchant"] for d in data["details"]} == {"奶茶店"}


def test_api_rejects_extra_fields(client):
    """非白名单字段一律拒绝（extra=forbid → 422），user_id 无法从请求体注入"""
    resp = client.post(
        "/api/nl-query",
        json={"question": "本月支出", "user_id": USER_B},
        headers=A_HEADERS,
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [{"question": ""}, {"question": "花" * 201}, {}],
    ids=["empty", "too-long", "missing"],
)
def test_api_rejects_bad_question(client, payload):
    assert (
        client.post("/api/nl-query", json=payload, headers=A_HEADERS).status_code == 422
    )


# ---- DAO：聚合与商户子串匹配 ----


def test_nl_aggregate_merchant_escape(db):
    """商户子串匹配对 LIKE 通配符转义：关键词含 % 只按字面匹配"""
    from app.db.dao.stat_dao import StatDAO

    _seed(
        [
            ("2026-08-05 12:00:00", "expense", 10.0, "购物", "100%满意超市"),
            ("2026-08-06 12:00:00", "expense", 20.0, "购物", "100满意超市"),
        ],
        USER_A,
        "NLE",
    )
    agg = StatDAO.nl_aggregate(
        USER_A,
        start="2026-08-01",
        end="2026-08-31",
        merchants=["100%满"],
        tx_type="expense",
    )
    assert agg["count"] == 1
    assert agg["total"] == 10.0
    # 不转义的话 % 会当通配符，两条都命中
    agg_all = StatDAO.nl_aggregate(
        USER_A,
        start="2026-08-01",
        end="2026-08-31",
        merchants=["100满意"],
        tx_type="expense",
    )
    assert agg_all["count"] == 1


def test_nl_aggregate_month_group_and_count_metric(db):
    """按月分组键为 YYYY-MM；count 指标不筛 tx_type，金额合计为全类型"""
    from app.db.dao.stat_dao import StatDAO

    _seed(
        [
            ("2026-08-05 12:00:00", "expense", 10.0, "餐饮", "A"),
            ("2026-08-06 12:00:00", "income", 50.0, "其他", "B"),
            ("2026-07-01 12:00:00", "expense", 5.0, "餐饮", "C"),
        ],
        USER_A,
        "NLM",
    )
    agg = StatDAO.nl_aggregate(USER_A, group_by="month", order_by="key_asc", limit=10)
    assert [(r["key"], int(r["count"])) for r in agg["rows"]] == [
        ("2026-07", 1),
        ("2026-08", 2),
    ]
    assert agg["total"] == 65.0  # 收入+支出合计（count 指标口径）
    assert agg["count"] == 3


def test_nl_detail_rows_filters_and_order(db):
    """明细样本：同筛选按金额降序，只含当前账号"""
    from app.db.dao.bill_dao import BillDAO

    _seed(
        [
            ("2026-08-05 12:00:00", "expense", 10.0, "餐饮", "小店"),
            ("2026-08-06 12:00:00", "expense", 30.0, "餐饮", "小店"),
            ("2026-08-07 12:00:00", "expense", 20.0, "餐饮", "别家"),
        ],
        USER_A,
        "NLD",
    )
    rows = BillDAO.nl_detail_rows(
        USER_A,
        start="2026-08-01",
        end="2026-08-31",
        categories=["餐饮"],
        merchants=["小店"],
    )
    assert [r["amount"] for r in rows] == [30.0, 10.0]
    assert all(r["merchant"] == "小店" for r in rows)


# ---- T-6.2 对话式查账：追问上下文（保留 3 轮）----


def _hist(result: dict) -> list[dict]:
    """把上一轮结果转成追问上下文（前端回传形态）"""
    return [{"question": result["question"], "spec": result["spec"]}]


def test_followup_inherits_time(db):
    """ "那收入呢"：时间未表达则继承；指代词触发分类继承，指标切换为收入"""
    first = nl_service.query(USER_A, "上个月餐饮支出多少", today=TODAY)
    assert first["spec"]["time"]["start"] == "2026-08-01"
    follow = nl_service.query(USER_A, "那收入呢", history=_hist(first), today=TODAY)
    assert follow["source"] == "rule"
    assert follow["inherited"] == ["time", "categories"]
    assert follow["spec"]["time"]["start"] == "2026-08-01"
    assert follow["spec"]["categories"] == ["餐饮"]
    assert follow["spec"]["metric"] == "income"


def test_followup_reference_inherits_merchant(db):
    """含指代词且未提及商户/分类 → 继承上一轮商户；本轮已表达时间则不继承时间"""
    first = nl_service.query(USER_A, "上个月奶茶花了多少", today=TODAY)
    follow = nl_service.query(USER_A, "那这个月呢", history=_hist(first), today=TODAY)
    assert follow["spec"]["merchants"] == ["奶茶"]
    assert follow["inherited"] == ["merchants"]
    assert follow["spec"]["time"]["start"] == "2026-09-01"  # 本轮时间以本轮为准


def test_no_reference_no_merchant_inheritance(db):
    """无指代词的全新问题不被静默套上旧商户口径（时间仍按规则继承）"""
    first = nl_service.query(USER_A, "上个月奶茶花了多少", today=TODAY)
    follow = nl_service.query(USER_A, "收入多少", history=_hist(first), today=TODAY)
    assert follow["spec"]["merchants"] == []
    assert follow["spec"]["categories"] == []
    assert "merchants" not in follow["inherited"]


def test_history_spec_sanitized(db):
    """回传的 spec 被篡改（非法分类/非法枚举/超长商户）→ 白名单收敛，不炸不越权"""
    tampered = {
        "question": "上个月奶茶花了多少",
        "spec": {
            "time": {"start": "2026-08-01", "end": "2026-08-31", "label": "8月"},
            "categories": ["不存在的分类"],
            "merchants": ["x" * 100],
            "metric": "sql",
            "group_by": "evil",
            "order_by": "amount_desc",
            "limit": 5,
        },
    }
    result = nl_service.query(USER_A, "那这个月呢", history=[tampered], today=TODAY)
    assert result["spec"]["categories"] == []
    assert all(len(m) <= 64 for m in result["spec"]["merchants"])
    assert result["spec"]["metric"] == "expense"  # 非法枚举兜底默认值


def test_llm_history_in_prompt(db, monkeypatch):
    """LLM 路径：追问上下文写进提示词供模型解析指代，输出仍过白名单

    上一轮口径无时间且本轮无指代词时，规则路径无可继承 → 让位给模型；
    有可继承维度的追问在规则路径就被截住（见 test_followup_rule_path_avoids_llm）。
    """
    calls: list = []
    _install_llm(monkeypatch, {"metric": "expense"}, calls)
    history = [
        {
            "question": "美团一共花了多少",
            "spec": {
                "time": {"start": None, "end": None, "label": "全部时间"},
                "categories": [],
                "merchants": ["美团"],
                "metric": "expense",
                "group_by": "none",
                "order_by": "amount_desc",
                "limit": 10,
            },
        }
    ]
    result = nl_service.query(USER_A, "整体情况如何", history=history, today=TODAY)
    assert result["source"] == "llm"
    system = calls[0]["messages"][0]["content"]
    assert "美团一共花了多少" in system
    assert "美团" in system


def test_followup_rule_path_avoids_llm(db, monkeypatch):
    """追问在规则路径就地继承口径：已配置模型也不发起调用（零配额消耗）"""
    calls: list = []
    _install_llm(monkeypatch, {"metric": "expense"}, calls)
    first = nl_service.query(USER_A, "上个月餐饮支出多少", today=TODAY)
    follow = nl_service.query(USER_A, "那笔数呢", history=_hist(first), today=TODAY)
    assert follow["source"] == "rule"
    assert calls == []
    assert follow["spec"]["metric"] == "count"
    assert follow["spec"]["time"]["start"] == "2026-08-01"
    assert follow["spec"]["categories"] == ["餐饮"]


def test_api_followup_and_validation(client):
    """接口层：追问上下文可用；history 超 3 轮或夹带额外字段 → 422"""
    res = client.post(
        "/api/nl-query",
        headers=A_HEADERS,
        json={"question": "上个月餐饮支出多少"},
    )
    assert res.status_code == 200
    first = res.json()["data"]

    res = client.post(
        "/api/nl-query",
        headers=A_HEADERS,
        json={"question": "那收入呢", "history": _hist(first)},
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["inherited"] == ["time", "categories"]
    assert data["spec"]["time"]["start"] == "2026-08-01"

    # 超过 3 轮
    four = _hist(first) * 4
    assert (
        client.post(
            "/api/nl-query",
            headers=A_HEADERS,
            json={"question": "那收入呢", "history": four},
        ).status_code
        == 422
    )
    # history 条目夹带额外字段
    bad_hist = [{**_hist(first)[0], "user_id": USER_B}]
    assert (
        client.post(
            "/api/nl-query",
            headers=A_HEADERS,
            json={"question": "那收入呢", "history": bad_hist},
        ).status_code
        == 422
    )
