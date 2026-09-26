"""分类扩展 AI 任务测试（v1.1）：T-A 关键词生成、T-B 子类方案、CAP-3 自动建分类

外部调用全部 mock（_chat），不发起真实网络请求。核心是方案 §12 的
「开关守卫」：auto_category_enabled=False 时白名单外结果必须丢弃且分类表
零新增；开启时配额（单批 ≤2、≥2 笔提名）生效。
"""

import json

import pytest

from app.db.dao.bill_dao import BillDAO
from app.db.dao.category_dao import CategoryDAO
from app.db.dao.category_keyword_dao import CategoryKeywordDAO
from app.file_settings import AISettings
from app.services import ai_service
from tests.conftest import USER_A, make_bill_records

CFG = AISettings(api_key="test-key", base_url="https://api.example.com")
CFG_CREATE = AISettings(api_key="test-key", auto_category_enabled=True)
CFG_SUB = AISettings(
    api_key="test-key", auto_category_enabled=True, auto_subcategory_enabled=True
)


def _chat_returning(payload: dict, calls: list | None = None):
    def fake(settings, messages, max_tokens, timeout=60):
        if calls is not None:
            calls.append(messages)
        return json.dumps(payload, ensure_ascii=False)

    return fake


def _add_keyword(category_name, word):
    CategoryKeywordDAO.create_many(
        CategoryDAO.get_by_name(category_name)["id"], [word], "manual"
    )


@pytest.fixture(autouse=True)
def fake_ai_ready(monkeypatch):
    """生成类任务的密钥前置一律放行（load_ai_settings 指向固定已配置实例）

    classify_records / _auto_backfill_keywords 显式接收 settings，不受影响；
    generate_keyword_candidates / generate_subcategory_plan 内部经
    _require_ready 读真实配置（测试环境无 Key），统一替换。
    """
    monkeypatch.setattr(ai_service, "load_ai_settings", lambda: CFG)


# ---- CAP-3：开关守卫（核心）----


def test_allow_create_off_drops_new_names(db, monkeypatch):
    monkeypatch.setattr(
        ai_service,
        "_chat",
        _chat_returning({"result": {"0": "喵喵烘焙", "1": "餐饮", "2": "喵喵烘焙"}}),
    )
    records = make_bill_records(3)
    result = ai_service.classify_records(records, ["餐饮", "交通"], CFG)
    assert result == {1: "餐饮"}  # 白名单外一律丢弃
    assert CategoryDAO.get_by_name("喵喵烘焙") is None  # 分类表零新增


def test_allow_create_two_nominations_creates(db, monkeypatch):
    monkeypatch.setattr(
        ai_service,
        "_chat",
        _chat_returning({"result": {"0": "喵喵烘焙", "1": "喵喵烘焙", "2": "餐饮"}}),
    )
    records = make_bill_records(3)
    result = ai_service.classify_records(
        records, ["餐饮", "交通"], CFG_CREATE, allow_create=True
    )
    assert result == {0: "喵喵烘焙", 1: "喵喵烘焙", 2: "餐饮"}
    created = CategoryDAO.get_by_name("喵喵烘焙")
    assert created is not None
    assert created["source"] == "ai"
    assert created["parent_id"] is None


def test_allow_create_single_nomination_not_created(db, monkeypatch):
    monkeypatch.setattr(
        ai_service,
        "_chat",
        _chat_returning({"result": {"0": "喵喵烘焙", "1": "餐饮"}}),
    )
    result = ai_service.classify_records(
        make_bill_records(2), ["餐饮", "交通"], CFG_CREATE, allow_create=True
    )
    assert result == {1: "餐饮"}  # 仅 1 笔提名：抗单次幻觉，不建
    assert CategoryDAO.get_by_name("喵喵烘焙") is None


def test_allow_create_quota_two_per_batch(db, monkeypatch):
    monkeypatch.setattr(
        ai_service,
        "_chat",
        _chat_returning(
            {
                "result": {
                    "0": "喵喵烘焙",
                    "1": "喵喵烘焙",
                    "2": "喵喵洗车",
                    "3": "喵喵洗车",
                    "4": "喵喵众筹",
                    "5": "餐饮",
                }
            }
        ),
    )
    result = ai_service.classify_records(
        make_bill_records(6), ["餐饮", "交通"], CFG_CREATE, allow_create=True
    )
    # 提名达标的按票数取前 2；1 票的「喵喵众筹」落空
    assert CategoryDAO.get_by_name("喵喵烘焙") is not None
    assert CategoryDAO.get_by_name("喵喵洗车") is not None
    assert CategoryDAO.get_by_name("喵喵众筹") is None
    assert result[5] == "餐饮"


def test_subcategory_created_when_enabled(db, monkeypatch):
    monkeypatch.setattr(
        ai_service,
        "_chat",
        _chat_returning({"result": {"0": "餐饮/喵喵烘焙", "1": "餐饮/喵喵烘焙"}}),
    )
    result = ai_service.classify_records(
        make_bill_records(2), ["餐饮", "交通"], CFG_SUB, allow_create=True
    )
    created = CategoryDAO.get_by_name("喵喵烘焙")
    assert created is not None
    assert created["parent_id"] == CategoryDAO.get_by_name("餐饮")["id"]
    assert result[0] == "喵喵烘焙"


def test_slash_name_without_subcategory_flag_lands_top_level(db, monkeypatch):
    """auto_subcategory_enabled=False：斜杠层级表达被忽略，新类建在顶层"""
    monkeypatch.setattr(
        ai_service,
        "_chat",
        _chat_returning({"result": {"0": "餐饮/喵喵烘焙", "1": "餐饮/喵喵烘焙"}}),
    )
    ai_service.classify_records(
        make_bill_records(2), ["餐饮", "交通"], CFG_CREATE, allow_create=True
    )
    created = CategoryDAO.get_by_name("喵喵烘焙")
    assert created is not None
    assert created["parent_id"] is None


def test_overlong_new_name_truncated(db, monkeypatch):
    long_name = "喵" * 30
    monkeypatch.setattr(
        ai_service,
        "_chat",
        _chat_returning({"result": {"0": long_name, "1": long_name}}),
    )
    ai_service.classify_records(
        make_bill_records(2), ["餐饮", "交通"], CFG_CREATE, allow_create=True
    )
    created = CategoryDAO.get_by_name("喵" * 20)
    assert created is not None  # 截断到 CATEGORY_NAME_MAX_LENGTH


# ---- T-A：关键词生成（两段式第一步）----


def test_generate_keywords_candidates_with_conflict(db, monkeypatch):
    BillDAO.insert_many(
        make_bill_records(3, merchant="喵喵烘焙坊", category="餐饮"), USER_A
    )
    _add_keyword("餐饮", "喵喵烘焙坊")  # 已有词：静默去重
    _add_keyword("购物", "喵喵")  # 跨分类同词：标记冲突
    calls: list = []
    monkeypatch.setattr(
        ai_service,
        "_chat",
        _chat_returning({"keywords": ["喵喵", "喵喵烘焙坊", "a", "喵喵面包"]}, calls),
    )
    result = ai_service.generate_keyword_candidates(
        CategoryDAO.get_by_name("餐饮")["id"]
    )
    by_word = {c["keyword"]: c["conflict"] for c in result["candidates"]}
    assert by_word["喵喵"] == "购物"  # 冲突标记
    assert by_word["喵喵面包"] is None
    assert "喵喵烘焙坊" not in by_word  # 本分类已有词不重复给出
    assert "a" not in by_word  # 非法词被清洗
    assert result["sample_size"] == 1
    # 隐私红线：prompt 只含商户名，不含金额/日期/备注
    user_content = calls[0][-1]["content"]
    assert "喵喵烘焙坊" in user_content
    assert "金额" not in user_content and "备注" not in user_content


def test_generate_keywords_requires_samples(db, monkeypatch):
    with pytest.raises(ai_service.AIClientError):
        ai_service.generate_keyword_candidates(CategoryDAO.get_by_name("餐饮")["id"])


def test_generate_keywords_missing_field_raises(db, monkeypatch):
    BillDAO.insert_many(
        make_bill_records(1, merchant="喵喵烘焙坊", category="餐饮"), USER_A
    )
    monkeypatch.setattr(ai_service, "_chat", _chat_returning({"foo": []}))
    with pytest.raises(ai_service.AIClientError):
        ai_service.generate_keyword_candidates(CategoryDAO.get_by_name("餐饮")["id"])


def test_generate_keywords_tolerates_code_fence(db, monkeypatch):
    BillDAO.insert_many(
        make_bill_records(1, merchant="喵喵烘焙坊", category="餐饮"), USER_A
    )

    def fake(settings, messages, max_tokens, timeout=60):
        return '```json\n{"keywords": ["喵喵词"]}\n```'

    monkeypatch.setattr(ai_service, "_chat", fake)
    result = ai_service.generate_keyword_candidates(
        CategoryDAO.get_by_name("餐饮")["id"]
    )
    assert result["candidates"][0]["keyword"] == "喵喵词"


def test_apply_keywords_writes_ai_source(db):
    result = ai_service.apply_keywords(
        CategoryDAO.get_by_name("餐饮")["id"], ["喵喵词"]
    )
    assert result["added"] == 1
    row = [
        r
        for r in CategoryKeywordDAO.list_by_category(
            CategoryDAO.get_by_name("餐饮")["id"]
        )
        if r["keyword"] == "喵喵词"
    ][0]
    assert row["source"] == "ai"


# ---- T-B：子类方案（两段式）----


def test_generate_children_cleans_model_output(db, monkeypatch):
    BillDAO.insert_many(
        make_bill_records(2, merchant="喵喵烘焙坊", category="餐饮"), USER_A
    )
    raw = {
        "children": [
            {
                "name": "喵喵烘焙",
                "keywords": ["喵喵包", "a"],
                "reason": "商户集中在烘焙",
            },
            {"name": "喵喵烘焙", "keywords": ["重复"], "reason": "重名"},  # 兄弟重名
            {"name": "餐饮", "keywords": ["x"], "reason": "与父重名"},  # 与父重名
            {"name": "交通", "keywords": ["地铁"], "reason": "与已有分类重名"},
            {"name": "无词子类", "keywords": [], "reason": "清洗后为空"},
            {
                "name": "超" * 30,
                "keywords": ["喵喵超长"],
                "reason": "名字超长截断",
            },
        ]
    }
    monkeypatch.setattr(ai_service, "_chat", _chat_returning(raw))
    result = ai_service.generate_subcategory_plan(CategoryDAO.get_by_name("餐饮")["id"])
    names = [c["name"] for c in result["children"]]
    assert names == ["喵喵烘焙", "超" * 20]  # 重名剔除 + 截断到列宽；空词组剔除
    assert result["children"][0]["keywords"] == ["喵喵包"]  # 非法词清洗
    assert result["bill_count"] == 2


def test_generate_children_truncates_to_five(db, monkeypatch):
    BillDAO.insert_many(
        make_bill_records(1, merchant="喵喵烘焙坊", category="餐饮"), USER_A
    )
    raw = {
        "children": [
            {"name": f"子类{i}", "keywords": ["喵喵词"], "reason": ""} for i in range(8)
        ]
    }
    monkeypatch.setattr(ai_service, "_chat", _chat_returning(raw))
    result = ai_service.generate_subcategory_plan(CategoryDAO.get_by_name("餐饮")["id"])
    assert len(result["children"]) == 5


def test_generate_children_rejects_child_category(db):
    from app.core.errors import ValidationError

    CategoryDAO.ensure_many(["临时父分类"])
    parent_id = CategoryDAO.get_by_name("临时父分类")["id"]
    child_id = CategoryDAO.create("临时子分类", parent_id=parent_id)
    with pytest.raises(ValidationError):
        ai_service.generate_subcategory_plan(child_id)
    with pytest.raises(ValidationError):
        ai_service.apply_subcategories(
            child_id, [{"name": "x", "keywords": ["喵喵词"]}]
        )


def test_apply_subcategories_and_migrate(db):
    parent_id = CategoryDAO.get_by_name("餐饮")["id"]
    BillDAO.insert_many(
        make_bill_records(3, merchant="喵喵烘焙坊", category="餐饮"), USER_A
    )
    BillDAO.insert_many(
        make_bill_records(2, prefix="T9", merchant="老王包子铺", category="餐饮"),
        USER_A,
    )
    result = ai_service.apply_subcategories(
        parent_id,
        [{"name": "喵喵烘焙", "keywords": ["喵喵烘焙坊"], "reason": ""}],
        migrate_bills=True,
    )
    assert [c["name"] for c in result["created"]] == ["喵喵烘焙"]
    assert result["migrated"] == 3
    child = CategoryDAO.get_by_name("喵喵烘焙")
    assert child["parent_id"] == parent_id and child["source"] == "ai"
    total, rows = BillDAO.list_bills(USER_A, category="喵喵烘焙")
    assert total == 3  # 命中的迁入子类
    total_left, _ = BillDAO.list_bills(USER_A, category="餐饮")
    assert total_left == 2  # 未命中的原地保留（D-1：迁移显式触发）
    kw = CategoryKeywordDAO.list_by_category(child["id"])
    assert kw[0]["source"] == "ai"


def test_apply_subcategories_skips_existing_name(db):
    from app.core.errors import ValidationError

    parent_id = CategoryDAO.get_by_name("餐饮")["id"]
    # 「交通」已是既有分类：清洗阶段整组剔除，全部落空时整体拒绝（400）
    with pytest.raises(ValidationError):
        ai_service.apply_subcategories(
            parent_id,
            [
                {
                    "name": "交通",
                    "keywords": ["喵喵词"],
                    "reason": "与已有顶层分类重名",
                },
            ],
        )


# ---- auto_keyword_enabled：归类顺带回填关键词（批内变体）----


def test_auto_backfill_writes_ai_keywords(db, monkeypatch):
    calls: list = []

    def fake(settings, messages, max_tokens, timeout=60):
        calls.append(messages)
        return json.dumps({"keywords": {"餐饮": ["喵喵", "a"]}}, ensure_ascii=False)

    monkeypatch.setattr(ai_service, "_chat", fake)
    settings = AISettings(api_key="k", auto_keyword_enabled=True)
    records = [
        {"merchant": "喵喵烘焙", "remark": "", "tx_type": "expense", "amount": 10}
    ]
    ai_service._auto_backfill_keywords(settings, records, {0: "餐饮"})
    assert len(calls) == 1
    user_content = calls[0][-1]["content"]
    assert "喵喵烘焙" in user_content and "金额" not in user_content
    rows = [
        r
        for r in CategoryKeywordDAO.list_by_category(
            CategoryDAO.get_by_name("餐饮")["id"]
        )
        if r["keyword"] == "喵喵"
    ]
    assert rows and rows[0]["source"] == "ai"


def test_auto_backfill_disabled_makes_no_call(db, monkeypatch):
    def fake(settings, messages, max_tokens, timeout=60):
        raise AssertionError("开关关闭时不得发起请求")

    monkeypatch.setattr(ai_service, "_chat", fake)
    settings = AISettings(api_key="k", auto_keyword_enabled=False)
    ai_service._auto_backfill_keywords(settings, [{"merchant": "喵喵"}], {0: "餐饮"})


def test_auto_backfill_failure_is_silent(db, monkeypatch):
    def fake(settings, messages, max_tokens, timeout=60):
        raise ai_service.AIClientError("网络故障")

    monkeypatch.setattr(ai_service, "_chat", fake)
    settings = AISettings(api_key="k", auto_keyword_enabled=True)
    # 不抛异常即通过
    ai_service._auto_backfill_keywords(settings, [{"merchant": "喵喵"}], {0: "餐饮"})
