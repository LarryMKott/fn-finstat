"""智能分类（DeepSeek）测试：配置读写、客户端容错、导入二次归类、存量批量重分类

外部 HTTP 全部通过 monkeypatch 替换（_chat / urllib.request.urlopen），
单测不发起真实网络请求；AI 配置由 conftest.ai_config_isolated 指向临时文件。
"""

import io
import json
import os
import urllib.error

import pytest

# 测试专用 API Key：从环境变量读取（默认值为非可用凭据的占位串，不存在泄露风险）
TEST_API_KEY = os.environ.get("TEST_AI_API_KEY", "test-key-not-usable")

from app.file_settings import AISettings, save_ai_settings
from app.db.dao.bill_dao import BillDAO
from app.schemas.ai import AITestResult
from app.services import ai_service
from tests.conftest import USER_A, USER_B, make_bill_records
from conftest import assert_report

CFG = AISettings(
    api_key=TEST_API_KEY, base_url="https://api.example.com", model="deepseek-chat"
)

A_HEADERS = {
    "X-Trim-Userid": USER_A,
    "X-Trim-Username": "zhangsan",
    "X-Trim-Isadmin": "true",
}
B_HEADERS = {"X-Trim-Userid": USER_B}


def build_xlsx_bytes(rows: list[list]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


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


def wechat_rows(*merchants: str) -> bytes:
    body = [
        [
            "2024-01-01 08:30:00",
            "商户消费",
            merchant,
            "商品",
            "支出",
            "¥9.90",
            "零钱",
            "支付成功",
            f"AI-{i:04d}",
            "",
            "",
        ]
        for i, merchant in enumerate(merchants)
    ]
    return build_xlsx_bytes([WECHAT_HEADER, *body])


# ---------- AI 返回解析 ----------


def test_parse_assignments_keeps_valid_only():
    content = json.dumps(
        {
            "result": {
                "0": "餐饮",
                "1": "编造分类",
                "2": " 交通 ",
                "x": "购物",
                "99": "购物",
            }
        }
    )
    result = ai_service._parse_assignments(content, 3, {"餐饮", "交通"})
    assert result == {0: "餐饮", 2: "交通"}


def test_parse_assignments_strips_code_fence():
    content = '```json\n{"result": {"0": "餐饮"}}\n```'
    assert ai_service._parse_assignments(content, 1, {"餐饮"}) == {0: "餐饮"}


def test_parse_assignments_invalid_json_raises():
    with pytest.raises(ai_service.AIClientError):
        ai_service._parse_assignments("不是 JSON", 1, {"餐饮"})


def test_parse_assignments_missing_result_raises():
    with pytest.raises(ai_service.AIClientError):
        ai_service._parse_assignments('{"foo": {}}', 1, {"餐饮"})


# ---------- DeepSeek 客户端 ----------


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_chat_request_format_and_response(monkeypatch, outbound_guard_bypass):
    captured = {}
    body = json.dumps(
        {"choices": [{"message": {"content": '{"result": {"0": "餐饮"}}'}}]}
    ).encode()

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResp(body)

    monkeypatch.setattr(ai_service, "_open", fake_urlopen)
    content = ai_service._chat(CFG, [{"role": "user", "content": "hi"}], 16)
    assert content == '{"result": {"0": "餐饮"}}'
    assert captured["request"].full_url == "https://api.example.com/chat/completions"
    assert captured["request"].get_header("Authorization") == f"Bearer {TEST_API_KEY}"
    assert captured["timeout"] == ai_service.REQUEST_TIMEOUT
    payload = json.loads(captured["request"].data.decode("utf-8"))
    assert payload["model"] == "deepseek-chat"
    assert payload["response_format"] == {"type": "json_object"}


def test_chat_maps_http_error(monkeypatch, outbound_guard_bypass):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"error": {"message": "invalid api key"}}'),
        )

    monkeypatch.setattr(ai_service, "_open", fake_urlopen)
    with pytest.raises(ai_service.AIClientError, match="401"):
        ai_service._chat(CFG, [{"role": "user", "content": "hi"}], 16)


def test_chat_maps_network_error(monkeypatch, outbound_guard_bypass):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(ai_service, "_open", fake_urlopen)
    with pytest.raises(ai_service.AIClientError, match="无法连接"):
        ai_service._chat(CFG, [{"role": "user", "content": "hi"}], 16)


def test_chat_maps_bad_response_shape(monkeypatch, outbound_guard_bypass):
    monkeypatch.setattr(ai_service, "_open", lambda req, timeout: FakeResp(b"{}"))
    with pytest.raises(ai_service.AIClientError, match="响应格式异常"):
        ai_service._chat(CFG, [{"role": "user", "content": "hi"}], 16)


def test_test_connection_ok_and_fail(monkeypatch):
    monkeypatch.setattr(ai_service, "_chat", lambda s, m, max_tokens: '{"ok": true}')
    assert ai_service.test_connection(CFG).ok is True

    def boom(s, m, max_tokens):
        raise ai_service.AIClientError("DeepSeek 接口返回 401：鉴权失败")

    monkeypatch.setattr(ai_service, "_chat", boom)
    result = ai_service.test_connection(CFG)
    assert result.ok is False and "401" in result.message


# ---------- 批量归类（分批 + 容错）----------


def test_classify_records_builds_prompt(monkeypatch):
    captured = {}

    def fake_chat(settings, messages, max_tokens):
        captured["messages"] = messages
        return json.dumps({"result": {"0": "餐饮", "1": "交通"}})

    monkeypatch.setattr(ai_service, "_chat", fake_chat)
    records = [
        {"merchant": "肯德基", "remark": "午餐", "tx_type": "expense", "amount": 30},
        {"merchant": "陌生商户", "remark": "", "tx_type": "expense", "amount": 5},
    ]
    assert ai_service.classify_records(records, ["餐饮", "交通"], CFG) == {
        0: "餐饮",
        1: "交通",
    }
    user_prompt = captured["messages"][1]["content"]
    assert "候选分类：餐饮、交通" in user_prompt
    assert "肯德基" in user_prompt and "陌生商户" in user_prompt


def test_classify_records_skips_when_not_ready():
    assert (
        ai_service.classify_records([{"merchant": "x"}], ["餐饮"], AISettings()) == {}
    )


def test_classify_batches_offsets_indices_across_batches(monkeypatch):
    """第二批的下标必须平移批大小，避免错改第一批的记录"""

    def fake_chat(settings, messages, max_tokens):
        # 从 user prompt 解析本批记录数，全部归为"购物"
        count = messages[1]["content"].count("\n") - 2
        return json.dumps({"result": {str(i): "购物" for i in range(count)}})

    monkeypatch.setattr(ai_service, "_chat", fake_chat)
    records = [
        {"merchant": f"商户{i}", "remark": "", "tx_type": "expense", "amount": 1}
        for i in range(60)
    ]
    result, completed = ai_service.classify_batches(records, ["购物"], CFG)
    assert completed is True
    assert set(result) == set(range(60))
    assert all(cat == "购物" for cat in result.values())


def test_classify_batches_tolerates_batch_failure(monkeypatch):
    calls = {"n": 0}

    def fake_chat(settings, messages, max_tokens):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ai_service.AIClientError("网络抖动")
        return json.dumps({"result": {"0": "购物"}})

    monkeypatch.setattr(ai_service, "_chat", fake_chat)
    records = [
        {"merchant": f"商户{i}", "remark": "", "tx_type": "expense", "amount": 1}
        for i in range(60)
    ]
    result, completed = ai_service.classify_batches(records, ["购物"], CFG)
    assert completed is True
    assert result == {50: "购物"}


def test_classify_batches_respects_time_budget(monkeypatch):
    def fake_chat(settings, messages, max_tokens):
        return json.dumps({"result": {"0": "购物"}})

    monkeypatch.setattr(ai_service, "_chat", fake_chat)
    records = [
        {"merchant": f"商户{i}", "remark": "", "tx_type": "expense", "amount": 1}
        for i in range(60)
    ]
    result, completed = ai_service.classify_batches(
        records, ["购物"], CFG, time_budget=-1
    )
    assert completed is False
    assert result == {}


# ---------- 配置接口 ----------


def test_config_endpoints_roundtrip(client, tmp_path, outbound_guard_bypass):
    resp = client.get("/api/ai/config")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["enabled"] is False
    assert data["has_api_key"] is False
    assert data["provider"] == "deepseek"
    assert data["base_url"] == "https://api.deepseek.com"
    assert data["model"] == "deepseek-chat"
    # 供应商预置表随管理员视图下发（本地无网关头视为管理员）
    provider_values = [p["value"] for p in data["providers"]]
    assert provider_values[0] == "deepseek" and "custom" in provider_values

    resp = client.put(
        "/api/ai/config",
        json={
            "enabled": True,
            "api_key": TEST_API_KEY,
            "base_url": "https://api.example.com/v1/",
            "model": "deepseek-chat",
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["has_api_key"] is True
    assert data["api_key_hint"] == f"****{TEST_API_KEY[-4:]}"  # hint 取 key 末 4 位
    assert data["enabled"] is True
    assert data["base_url"] == "https://api.example.com/v1"  # 尾斜杠去除
    assert data["provider"] == "deepseek"  # 未传 provider 保持不变

    saved = json.loads((tmp_path / "ai_config.json").read_text(encoding="utf-8"))
    assert saved["api_key"].startswith("enc:")  # T-1.7 加密存储
    assert saved["enabled"] is True

    # 不传 api_key 保持不变；enabled 可单独修改
    resp = client.put("/api/ai/config", json={"enabled": False})
    assert resp.json()["data"]["has_api_key"] is True
    assert resp.json()["data"]["enabled"] is False

    # 空串表示清除密钥
    resp = client.put("/api/ai/config", json={"api_key": ""})
    assert resp.json()["data"]["has_api_key"] is False


def test_config_rejects_bad_base_url(client):
    resp = client.put("/api/ai/config", json={"base_url": "ftp://x"})
    assert resp.status_code == 400


def test_config_rejects_empty_model(client):
    resp = client.put("/api/ai/config", json={"model": "  "})
    assert resp.status_code == 400


def test_test_endpoint_falls_back_to_saved_key(client, monkeypatch):
    client.put("/api/ai/config", json={"api_key": TEST_API_KEY})
    seen = {}

    def fake_conn(settings):
        seen["key"] = settings.api_key
        return AITestResult(ok=True, message="连接成功")

    monkeypatch.setattr(ai_service, "test_connection", fake_conn)

    # 表单未填密钥 → 回退已保存密钥
    resp = client.post("/api/ai/test", json={})
    assert resp.status_code == 200
    assert resp.json()["data"]["ok"] is True
    assert seen["key"] == TEST_API_KEY

    # 表单填了新密钥 → 新密钥优先（尚未保存也能测试）
    resp = client.post("/api/ai/test", json={"api_key": TEST_API_KEY})
    assert seen["key"] == TEST_API_KEY


def test_test_endpoint_without_key(client):
    resp = client.post("/api/ai/test", json={})
    assert resp.status_code == 200
    assert resp.json()["data"] == {"ok": False, "message": "请先填写 API Key"}


# ---------- 存量流水批量重分类 ----------


def test_classify_endpoint_updates_unmatched_and_isolates_accounts(client, monkeypatch):
    BillDAO.insert_many(
        make_bill_records(2, prefix="AI-A", merchant="神秘商户A"), USER_A
    )
    BillDAO.insert_many(make_bill_records(1, prefix="AI-K", category="餐饮"), USER_A)
    BillDAO.insert_many(
        make_bill_records(1, prefix="AI-B", merchant="神秘商户B"), USER_B
    )

    def fake_batches(records, categories, settings, time_budget=None):
        # 仅收到 USER_A 的"其他"流水；候选分类来自分类表
        assert [r["merchant"] for r in records] == ["神秘商户A", "神秘商户A"]
        assert "餐饮" in categories
        return {0: "购物", 1: "交通"}, True

    monkeypatch.setattr(ai_service, "classify_batches", fake_batches)
    client.put("/api/ai/config", json={"api_key": TEST_API_KEY})

    resp = client.post(
        "/api/ai/classify", json={"scope": "unmatched"}, headers=A_HEADERS
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["processed"] == 2
    assert data["changed"] == 2

    _, rows = BillDAO.list_bills(USER_A)
    by_tx = {r["tx_id"]: r["category"] for r in rows}
    assert by_tx["AI-A-0000"] == "购物"
    assert by_tx["AI-A-0001"] == "交通"
    assert by_tx["AI-K-0000"] == "餐饮"  # 非"其他"分类的记录不动

    # 账号隔离：USER_B 的流水未被改写
    _, rows_b = BillDAO.list_bills(USER_B)
    assert rows_b[0]["category"] == "其他"


def test_classify_endpoint_requires_key(client):
    resp = client.post(
        "/api/ai/classify", json={"scope": "unmatched"}, headers=A_HEADERS
    )
    assert resp.status_code == 400
    assert "API Key" in resp.json()["msg"]


def test_classify_endpoint_no_pending_bills(client):
    client.put("/api/ai/config", json={"api_key": TEST_API_KEY})
    resp = client.post(
        "/api/ai/classify", json={"scope": "unmatched"}, headers=A_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {
        "processed": 0,
        "changed": 0,
        "message": "没有需要归类的流水",
        "completed": True,
        "next_after_id": None,
    }


def test_classify_endpoint_rejects_reentrant_run(client):
    """任务未结束时再次触发应被拒绝，而不是排队产生重复的 API 调用"""
    with ai_service._CLASSIFY_LOCK:
        resp = client.post(
            "/api/ai/classify", json={"scope": "unmatched"}, headers=A_HEADERS
        )
    assert resp.status_code == 400
    assert "进行中" in resp.json()["msg"]

    # 锁释放后恢复正常（未配 Key 时报未配置，而不是再报重入）
    resp = client.post(
        "/api/ai/classify", json={"scope": "unmatched"}, headers=A_HEADERS
    )
    assert "进行中" not in resp.json()["msg"]


# ---------- 导入时二次归类 ----------


def test_import_with_ai_enhancement(client, monkeypatch):
    enabled = AISettings(api_key=TEST_API_KEY, enabled=True)
    monkeypatch.setattr(ai_service, "load_ai_settings", lambda: enabled)

    def fake_batches(records, categories, settings, time_budget=None):
        # 仅关键词未命中（"其他"）的记录进入 AI 归类
        assert [r["merchant"] for r in records] == ["张三", "李四"]
        return {0: "宠物", 1: "宠物"}, True

    monkeypatch.setattr(ai_service, "classify_batches", fake_batches)

    resp = client.post(
        "/api/upload/wechat",
        files={
            "file": (
                "bill.xlsx",
                wechat_rows("张三", "李四"),
                "application/octet-stream",
            )
        },
        headers=A_HEADERS,
    )
    assert resp.status_code == 200
    assert_report(resp.json()["data"], total=2, inserted=2, ai_classified=2)

    _, rows = BillDAO.list_bills(USER_A)
    assert {r["category"] for r in rows} == {"宠物"}


def test_import_keeps_keyword_result_when_ai_disabled(client):
    resp = client.post(
        "/api/upload/wechat",
        files={"file": ("bill.xlsx", wechat_rows("张三"), "application/octet-stream")},
        headers=A_HEADERS,
    )
    assert resp.status_code == 200
    assert_report(resp.json()["data"], total=1, inserted=1)


def test_import_survives_ai_failure(client, monkeypatch):
    monkeypatch.setattr(
        ai_service, "load_ai_settings", lambda: AISettings(api_key="sk", enabled=True)
    )

    def boom(*args, **kwargs):
        raise ai_service.AIClientError("网络故障")

    monkeypatch.setattr(ai_service, "classify_batches", boom)

    resp = client.post(
        "/api/upload/wechat",
        files={"file": ("bill.xlsx", wechat_rows("张三"), "application/octet-stream")},
        headers=A_HEADERS,
    )
    assert resp.status_code == 200
    assert_report(resp.json()["data"], total=1, inserted=1)
    _, rows = BillDAO.list_bills(USER_A)
    assert rows[0]["category"] == "其他"  # 失败保留关键词结果


def test_test_endpoint_rejects_non_admin(client, monkeypatch):
    """评审 H-1：test 接口的 base_url 由调用方提供且出站携带共享 Key，
    对全部用户开放时任意账号可把共享 Key 外泄到自己控制的服务器，
    与保存接口同为管理员操作"""
    seen = {}

    def fake_conn(settings):
        seen["called"] = True
        return AITestResult(ok=True, message="连接成功")

    monkeypatch.setattr(ai_service, "test_connection", fake_conn)

    resp = client.post("/api/ai/test", json={}, headers=B_HEADERS)
    assert resp.status_code == 403
    assert "called" not in seen  # 请求未触达业务逻辑，共享 Key 不出站


def test_reclassify_all_cursor_continues(db, monkeypatch):
    """评审 M-9：scope=all 满页时返回游标，携带 after_id 续跑剩余流水，
    不再固定取前 N 条导致尾部永远不可达、重头重复计费"""
    save_ai_settings(AISettings(api_key=TEST_API_KEY))
    BillDAO.insert_many(make_bill_records(3, tx_id=None), USER_A)
    monkeypatch.setattr(ai_service, "CLASSIFY_LIMIT", 2)
    monkeypatch.setattr(
        ai_service, "_chat", lambda s, m, max_tokens: '{"result": {"0": "餐饮"}}'
    )

    cursor = None
    rounds = 0
    while rounds < 10:
        result = ai_service.reclassify_bills(USER_A, "all", after_id=cursor)
        if result["completed"]:
            break
        # 游标必须前进，否则同一页会被无限重复处理
        assert result["next_after_id"] not in (None, cursor)
        cursor = result["next_after_id"]
        rounds += 1

    assert result["completed"] is True
    _, rows = BillDAO.list_bills(USER_A)
    assert all(r["category"] == "餐饮" for r in rows)


def test_config_masks_details_for_non_admin(client, outbound_guard_bypass):
    """普通账号共享 AI 能力但不见配置内容：base_url 与密钥尾号仅管理员可见"""
    client.put(
        "/api/ai/config",
        json={"api_key": TEST_API_KEY, "base_url": "https://api.example.com/v1"},
        headers=A_HEADERS,
    )
    admin = client.get("/api/ai/config", headers=A_HEADERS).json()["data"]
    assert admin["base_url"] == "https://api.example.com/v1"
    assert admin["api_key_hint"].startswith("****")

    plain = client.get("/api/ai/config", headers=B_HEADERS).json()["data"]
    assert plain["base_url"] == ""
    assert plain["api_key_hint"] == ""
    assert plain["has_api_key"] is True  # 能力状态仍可见
    assert plain["model"]  # 模型名非敏感，保持可见


# ---------- 多供应商（AI 供应商注册表） ----------


def test_provider_roundtrip_and_preset_defaults(
    client, tmp_path, outbound_guard_bypass
):
    """切换供应商未显式带地址/模型时回填预置默认值，避免错配组合"""
    resp = client.put("/api/ai/config", json={"provider": "zhipu"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["provider"] == "zhipu"
    assert data["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert data["model"] == "glm-4.5"

    # 落盘持久化
    saved = json.loads((tmp_path / "ai_config.json").read_text(encoding="utf-8"))
    assert saved["provider"] == "zhipu"

    # 显式地址优先于预置默认
    resp = client.put(
        "/api/ai/config",
        json={"provider": "moonshot", "base_url": "https://api.example.com/v1"},
    )
    assert resp.json()["data"]["base_url"] == "https://api.example.com/v1"
    assert resp.json()["data"]["model"] == "moonshot-v1-8k"


def test_provider_unknown_rejected(client):
    resp = client.put("/api/ai/config", json={"provider": "not-a-vendor"})
    assert resp.status_code == 400


def test_load_settings_falls_back_for_unknown_provider(tmp_path, monkeypatch):
    """手改配置文件写入未知供应商时按 deepseek 兜底（读取路径的最后防线）"""
    import app.file_settings as fs

    monkeypatch.setattr(fs, "AI_CONFIG_FILE", tmp_path / "ai_config.json")
    fs.save_ai_settings(
        fs.AISettings(provider="zhipu", base_url="x", model="m", api_key="k")
    )
    raw = json.loads((tmp_path / "ai_config.json").read_text(encoding="utf-8"))
    raw["provider"] = "hack"
    (tmp_path / "ai_config.json").write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8"
    )
    settings = fs.load_ai_settings()
    assert settings.provider == "deepseek"
    # 显式存过的 base_url 不因 provider 兜底被丢弃
    assert settings.base_url == "x"
