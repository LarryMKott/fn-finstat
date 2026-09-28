"""家庭月度 AI 复盘端点测试（AI-10）：POST /api/family/ai-review

服务层口径（门控前移等）在 test_family_ai_review.py 覆盖，本文件钉：
端点存在性与响应结构、未配置 AI 的统一错误体、非成员拒绝。
"""

from app.db.dao.bill_dao import BillDAO
from app.services import family_ai_service, family_service
from tests.conftest import USER_A, USER_B, make_bill_records

from tests.api.test_api import A_HEADERS, B_HEADERS

MONTH = "2026-08"


def _seed_family_with_bills(client):
    """A 建家庭 + B 加入 + 双方记账（走 API 保证身份快照一致）"""
    created = client.post("/api/family", json={"name": "我们家"}, headers=A_HEADERS)
    code = created.json()["data"]["invite_code"]
    client.post("/api/family/join", json={"code": code}, headers=B_HEADERS)
    for uid, prefix in ((USER_A, "FAP-A"), (USER_B, "FAP-B")):
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=prefix,
                tx_time=f"{MONTH}-05 10:00:00",
                tx_type="expense",
                amount=1000,
                merchant="超市",
                category="餐饮",
            ),
            uid,
        )


def test_family_ai_review_requires_ai_config(client, db):
    _seed_family_with_bills(client)
    res = client.post("/api/family/ai-review", json={"month": MONTH}, headers=A_HEADERS)
    assert res.status_code == 400
    assert res.json()["code"] == 48001


def test_family_ai_review_returns_review(client, db, monkeypatch):
    from app.file_settings import AISettings

    _seed_family_with_bills(client)
    monkeypatch.setattr(
        family_ai_service, "load_ai_settings", lambda: AISettings(api_key="k")
    )
    monkeypatch.setattr(
        family_ai_service.ai_service,
        "chat",
        lambda s, m, max_tokens, timeout=60: "复盘正文",
    )
    res = client.post("/api/family/ai-review", json={"month": MONTH}, headers=A_HEADERS)
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["review"] == "复盘正文"
    assert data["month"] == MONTH
    assert data["member_detail_included"] is False
    assert "members" not in data["context"]


def test_family_ai_review_non_member_rejected(client, db, monkeypatch):
    from app.file_settings import AISettings

    _seed_family_with_bills(client)
    monkeypatch.setattr(
        family_ai_service, "load_ai_settings", lambda: AISettings(api_key="k")
    )
    monkeypatch.setattr(
        family_ai_service.ai_service,
        "chat",
        lambda s, m, max_tokens, timeout=60: "复盘正文",
    )
    res = client.post("/api/family/ai-review", json={"month": MONTH}, headers=B_HEADERS)
    # B 是成员能调；这里验证真正的外人（未加入任何家庭）被拒
    assert res.status_code == 200
    outsider = client.post(
        "/api/family/ai-review",
        json={"month": MONTH},
        headers={"X-Trim-Userid": "user-x"},
    )
    assert outsider.status_code == 404
