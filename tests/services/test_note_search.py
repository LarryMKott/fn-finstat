"""备注语义检索服务测试（AI-8）：语义命中 / 噪声处理 / 隔离与筛选

口径钉住脑洞清单 §7.4 的拍板路线：零依赖 TF-IDF（检索域 = 商户 + 备注），
「给家里人买东西」这类与备注无字面重叠的模糊问法要能命中语义相关流水；
相关度为相对排序分（0~1），只做排序不做硬过滤——单字巧合也会给分，
首条命中应显著高于无关流水。
"""

import pytest

from app.core.errors import BizError
from app.db.dao.bill_dao import BillDAO
from app.services import note_search_service
from app.utils.note_search import tokenize
from tests.conftest import USER_A, USER_B, make_bill_records

CORPUS = [
    # (商户, 备注)——「给家里人买东西」查询与多条备注无任何字面重叠
    ("天猫超市", "给老妈买的按摩仪"),
    ("京东商城", "父亲节礼物"),
    ("拼多多", "奶奶的降压药"),
    ("市第一人民医院", "门诊挂号"),
    ("老百姓大药房", "感冒药"),
    ("携程", "去上海的机票"),
    ("滴滴出行", "打车去机场"),
    ("新东方", "雅思培训报名"),
]


def _seed(user=USER_A, tx_type="expense") -> None:
    for i, (merchant, remark) in enumerate(CORPUS):
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"NS-{user[:4]}-{i:02d}",
                merchant=merchant,
                remark=remark,
                tx_type=tx_type,
            ),
            user,
        )


def test_semantic_hit_without_literal_overlap(db):
    """查询词组与备注无字面重叠也要命中：关键词检索的失效区正是本功能的靶心"""
    _seed()
    q = "给家里人买东西"
    q_grams = {q[j : j + 2] for j in range(len(q) - 1)}
    for merchant, remark in CORPUS[:2]:
        text = f"{merchant} {remark}"
        assert not any(
            g in text for g in q_grams
        ), "前提自检：语料不应与查询有词组级重叠"

    data = note_search_service.search_notes(USER_A, q)
    hits = [r["remark"] for r in data["results"]]
    assert 0 < len(hits) <= 10
    assert "给老妈买的按摩仪" in hits or "父亲节礼物" in hits
    # 首条命中相关度应显著（排序有意义，而非并列噪声）
    assert data["results"][0]["score"] > 0.1
    # 响应行结构与流水列表一致（含内部字段过滤）+ 附加相关度
    first = data["results"][0]
    assert {
        "id",
        "tx_time",
        "merchant",
        "amount",
        "category",
        "remark",
        "score",
    } <= set(first)
    assert "user_id" not in first and "deleted" not in first


def test_medical_query_hits_pharmacy_rows(db):
    """「看病买药」命中药房/医院流水——查询词本身不出现在备注里"""
    _seed()
    remarks = [
        r["remark"]
        for r in note_search_service.search_notes(USER_A, "看病买药")["results"]
    ]
    assert "感冒药" in remarks or "门诊挂号" in remarks


def test_no_match_returns_empty_and_caliber(db):
    """纯英文无词面交集返回空；口径（域/上限/分词）随响应回传"""
    _seed()
    data = note_search_service.search_notes(USER_A, "xyz")
    assert data["results"] == []
    assert data["indexed"] == len(CORPUS)
    assert data["caliber"]["fields"] == "商户+备注"
    assert data["caliber"]["top_k"] == 10
    assert data["caliber"]["capped"] is False


def test_top_k_clamped_and_corpus_cap_flagged(db, monkeypatch):
    """top_k 钳制 1~50；语料达上限时 capped 置位且只索引最近 N 条"""
    _seed()
    data = note_search_service.search_notes(USER_A, "买", top_k=999)
    assert len(data["results"]) <= 50
    assert data["caliber"]["top_k"] == 50

    monkeypatch.setattr(note_search_service, "NOTE_SEARCH_MAX_DOCS", 3)
    capped = note_search_service.search_notes(USER_A, "买")
    assert capped["indexed"] == 3
    assert capped["caliber"]["capped"] is True


def test_empty_or_blank_query_rejected(db):
    """空串/纯空白检索词按参数不合法拒绝（服务层兜底，API 层另有 422 校验）"""
    _seed()
    for bad in ("", "   "):
        with pytest.raises(BizError):
            note_search_service.search_notes(USER_A, bad)


def test_user_isolation(db):
    """账号隔离：B 检索不到 A 的流水；同查询互相不可见"""
    _seed(USER_A)
    data_b = note_search_service.search_notes(USER_B, "给家里人买东西")
    assert data_b["results"] == []
    assert data_b["indexed"] == 0
    # B 自己的语料只对 B 可见
    BillDAO.insert_many(
        make_bill_records(
            1, prefix="NS-B-OWN", merchant="花店", remark="给女儿的生日花束"
        ),
        USER_B,
    )
    hits_b = {
        r["remark"]
        for r in note_search_service.search_notes(USER_B, "给家里人买东西")["results"]
    }
    assert hits_b == {"给女儿的生日花束"}


def test_tx_type_filter(db):
    """tx_type 参与检索前过滤：支出查询不返回收入流水"""
    _seed(USER_A, tx_type="expense")
    BillDAO.insert_many(
        make_bill_records(
            1,
            prefix="NS-INC",
            tx_type="income",
            merchant="公司账户",
            remark="年终奖到账",
        ),
        USER_A,
    )
    hits = [
        r["remark"]
        for r in note_search_service.search_notes(USER_A, "年终奖到账")["results"]
    ]
    assert hits == ["年终奖到账"]
    hits_expense = [
        r["remark"]
        for r in note_search_service.search_notes(
            USER_A, "年终奖到账", tx_type="expense"
        )["results"]
    ]
    assert hits_expense == []
