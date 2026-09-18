"""分类规则自学习（T-6.3）测试：生成 / 命中 / 冲突 / 停用 / 优先级 + 管理接口

对应开发计划 T-6.3 与 REQ-AIC-001~003：
- 手动纠正同一商户分类 2 次后，新导入同类流水自动归入正确分类
- 该路径下不再调用 LLM（命中规则的流水不会进入 AI 二次归类）
- 规则可查看、可编辑、可停用
"""

import itertools
from pathlib import Path

import pytest

from app.config import DEFAULT_CATEGORY
from app.db.dao.bill_dao import BillDAO
from app.db.dao.learned_rule_dao import LearnedRuleDAO
from app.schemas.bill import BatchBillRequest, BillUpdate
from app.services import bill_service, learned_rule_service
from app.services.import_service import _parse_and_normalize
from tests.conftest import USER_A, make_bill_records

A_HEADERS = {"X-Trim-Userid": USER_A}
ADMIN_HEADERS = {"X-Trim-Userid": USER_A, "X-Trim-Isadmin": "true"}

_seed_seq = itertools.count()


def _seed_one(
    merchant: str, category: str = DEFAULT_CATEGORY, user_id: str = USER_A
) -> int:
    """造一笔流水并返回 id（tx_id 全局唯一，按自增序号生成前缀）"""
    rec = make_bill_records(
        1, prefix=f"LR{next(_seed_seq)}", merchant=merchant, category=category
    )[0]
    return BillDAO.create(rec, user_id)


def _correct_twice(merchant: str, category: str) -> None:
    """模拟「同一商户纠正两次」：造两笔该商户流水，各改一次分类"""
    for _ in range(2):
        bill_id = _seed_one(merchant)
        bill_service.update_bill(bill_id, BillUpdate(category=category), USER_A)


# ---- pattern 提取 ----


def test_extract_pattern_strips_brackets_and_noise():
    assert learned_rule_service.extract_pattern("瑞幸咖啡（朝阳门店）") == "瑞幸咖啡"
    assert learned_rule_service.extract_pattern("肯德基[外送]") == "肯德基"
    assert learned_rule_service.extract_pattern("  美团外卖- ") == "美团外卖"
    # 过短 / 空商户不产生规则
    assert learned_rule_service.extract_pattern("") == ""
    assert learned_rule_service.extract_pattern("超") == ""
    # 超长按列宽裁剪
    assert len(learned_rule_service.extract_pattern("店" * 100)) == 64


# ---- 证据生成与阈值 ----


def test_correction_to_default_category_not_learned(db):
    assert learned_rule_service.record_correction("某商户", DEFAULT_CATEGORY) is None
    assert LearnedRuleDAO.list_all() == []


def test_single_correction_is_candidate_not_active(db):
    """纠正 1 次只是候选（hits=1），不参与匹配——避免一次误改污染全库"""
    bill_id = _seed_one("瑞幸咖啡（朝阳门店）")
    bill_service.update_bill(bill_id, BillUpdate(category="餐饮"), USER_A)
    rules = LearnedRuleDAO.list_all()
    assert len(rules) == 1
    assert rules[0]["pattern"] == "瑞幸咖啡"
    assert rules[0]["category"] == "餐饮"
    assert rules[0]["hits"] == 1
    assert learned_rule_service.match("瑞幸咖啡（五道口店）") is None


def test_two_corrections_activate_rule(db):
    """验收主线：纠正 2 次后，新导入同类流水自动归入正确分类"""
    _correct_twice("瑞幸咖啡（朝阳门店）", "餐饮")
    records = [
        {"merchant": "瑞幸咖啡（五道口店）", "remark": "", "category": ""},
        {"merchant": "瑞幸咖啡 APP 点单", "remark": "", "category": ""},
    ]
    matched = learned_rule_service.apply_to_records(records)
    assert matched == 2
    assert all(r["category"] == "餐饮" for r in records)


def test_correction_same_category_no_evidence(db):
    """改成与原分类相同的分类不算纠正，不产生证据"""
    bill_id = _seed_one("瑞幸", category="餐饮")
    bill_service.update_bill(bill_id, BillUpdate(category="餐饮"), USER_A)
    assert LearnedRuleDAO.list_all() == []


# ---- 冲突：同 pattern 指向不同分类 ----


def test_conflict_resolves_by_hits(db):
    """同 pattern 不同分类：hits 高者生效；互相抵消（各 2 次）时先建者生效"""
    _correct_twice("瑞幸咖啡", "餐饮")  # 餐饮 hits=2（生效）
    _correct_twice("瑞幸咖啡", "购物")  # 购物 hits=2，平票
    by_cat = {r["category"]: r for r in LearnedRuleDAO.list_all()}
    assert by_cat["餐饮"]["hits"] == 2 and by_cat["购物"]["hits"] == 2
    # 平票时按先建者优先（id 小者）
    assert learned_rule_service.match("瑞幸咖啡（某某店）") == "餐饮"
    # 再纠正一次购物（hits=3）→ 反超生效
    bill_id = _seed_one("瑞幸咖啡")
    bill_service.update_bill(bill_id, BillUpdate(category="购物"), USER_A)
    assert learned_rule_service.match("瑞幸咖啡（某某店）") == "购物"


def test_longer_pattern_wins(db):
    """多 pattern 同时命中：更具体（更长）的商户名优先"""
    _correct_twice("瑞幸", "餐饮")
    _correct_twice("瑞幸咖啡朝阳店", "购物")
    records = [{"merchant": "瑞幸咖啡朝阳店", "remark": "", "category": ""}]
    learned_rule_service.apply_to_records(records)
    assert records[0]["category"] == "购物"


# ---- 停用 / 编辑 ----


def test_disable_and_re_enable(db):
    """规则可停用；用户再次纠正同一方向时自动恢复启用"""
    _correct_twice("瑞幸咖啡", "餐饮")
    rule = LearnedRuleDAO.list_all()[0]
    learned_rule_service.update_rule(rule["id"], None, False)
    assert learned_rule_service.match("瑞幸咖啡") is None
    # 再次纠正 = 推翻停用
    bill_id = _seed_one("瑞幸咖啡")
    bill_service.update_bill(bill_id, BillUpdate(category="餐饮"), USER_A)
    assert learned_rule_service.match("瑞幸咖啡") == "餐饮"
    assert LearnedRuleDAO.get(rule["id"])["enabled"] is True


def test_update_rule_category_and_delete(db):
    _correct_twice("瑞幸咖啡", "餐饮")
    rule = LearnedRuleDAO.list_all()[0]
    updated = learned_rule_service.update_rule(rule["id"], "购物", None)
    assert updated["category"] == "购物"
    assert learned_rule_service.match("瑞幸咖啡") == "购物"
    assert learned_rule_service.delete_rule(rule["id"]) is True
    assert learned_rule_service.match("瑞幸咖啡") is None
    assert learned_rule_service.delete_rule(rule["id"]) is False


def test_update_missing_rule_raises(db):
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        learned_rule_service.update_rule(999, "餐饮", None)


def test_rules_pointing_to_missing_category_inert(db):
    """规则指向的分类不存在时不再生效（分类被删后的兜底）"""
    _correct_twice("瑞幸咖啡", "餐饮")
    rule = LearnedRuleDAO.list_all()[0]
    LearnedRuleDAO.update_fields(rule["id"], {"category": "已删除的分类"})
    assert learned_rule_service.match("瑞幸咖啡") is None


# ---- 批量纠正 ----


def test_batch_set_category_learns_per_pattern_once(db):
    """批量纠正：一次动作对同一商户只记 1 次证据（不按流水条数膨胀）"""
    ids = [_seed_one("瑞幸咖啡") for _ in range(3)]
    ids.append(_seed_one("肯德基"))
    affected = bill_service.batch_action(
        BatchBillRequest(ids=ids, action="set_category", category="餐饮"), USER_A
    )
    assert affected == 4
    rules = {r["pattern"]: r["hits"] for r in LearnedRuleDAO.list_all()}
    assert rules == {"瑞幸咖啡": 1, "肯德基": 1}


def test_batch_skips_bills_already_in_target(db):
    """批量纠正时已在目标分类的流水不产生证据"""
    kept = _seed_one("瑞幸咖啡", category="餐饮")
    changed = _seed_one("肯德基", category="娱乐")
    bill_service.batch_action(
        BatchBillRequest(ids=[kept, changed], action="set_category", category="餐饮"),
        USER_A,
    )
    assert {r["pattern"] for r in LearnedRuleDAO.list_all()} == {"肯德基"}


# ---- 导入优先级：已学习规则 > 内置关键词 > LLM ----


class _FakeParser:
    """最小解析器桩：parse 返回预置记录（字段与真实解析器同构）"""

    def __init__(self, records):
        self._records = records

    def parse(self, path):
        return [dict(r) for r in self._records]


def test_import_priority_rule_over_keyword_and_parser(db, monkeypatch):
    """导入归类优先级：学习规则覆盖解析器自带分类与内置关键词命中

    AI 阶段入口被探针替换：捕获交给 AI 的分类列表，证明命中规则的流水
    （含原本会落入「其他」的）不会进入 LLM 归类路径。
    """
    captured: dict = {}

    def _fake_enhance(records):
        captured["categories"] = [r.get("category") for r in records]
        return 0

    monkeypatch.setattr("app.services.ai_service.enhance_import_records", _fake_enhance)
    _correct_twice("瑞幸咖啡", "宠物")  # 故意与内置关键词（咖啡→餐饮）不同

    records = [
        # 解析器自带分类 + 内置关键词都会给「餐饮」→ 学习规则必须覆盖
        {"merchant": "瑞幸咖啡（五道口店）", "remark": "", "category": "餐饮美食"},
        # 关键词未命中 → 若无学习规则会交给 LLM，命中规则后不进 AI 阶段
        {"merchant": "瑞幸咖啡 APP 点单", "remark": "", "category": ""},
        # 未命中任何规则的流水照常走关键词
        {"merchant": "滴滴出行", "remark": "", "category": ""},
    ]
    _records, normalized, _ai = _parse_and_normalize(
        _FakeParser(records), Path("dummy.xlsx"), "测试.xlsx"
    )
    by_merchant = {r["merchant"]: r["category"] for r in normalized}
    assert by_merchant["瑞幸咖啡（五道口店）"] == "宠物"
    assert by_merchant["瑞幸咖啡 APP 点单"] == "宠物"
    assert by_merchant["滴滴出行"] == "交通"
    # AI 阶段已没有任何「其他」流水待归类（本批全部由规则/关键词解决）
    assert DEFAULT_CATEGORY not in captured["categories"]


def test_rule_loading_failure_falls_back_to_keywords(db, monkeypatch):
    """规则读取异常时按无规则处理，导入主流程不受影响"""

    def _boom():
        raise RuntimeError("rules unavailable")

    monkeypatch.setattr("app.services.learned_rule_service._effective_rules", _boom)
    records = [{"merchant": "滴滴出行", "remark": "", "category": ""}]
    _records, normalized, _ai = _parse_and_normalize(
        _FakeParser(records), Path("dummy.xlsx"), "测试.xlsx"
    )
    assert normalized[0]["category"] == "交通"


# ---- 管理接口（列表可读 / 编辑删除仅管理员）----


def test_api_rule_management_permissions(client, db):
    _correct_twice("瑞幸咖啡", "餐饮")
    rule = LearnedRuleDAO.list_all()[0]

    # 列表：普通账号可读
    resp = client.get("/api/settings/learned-rules", headers=A_HEADERS)
    assert resp.status_code == 200
    items = resp.json()["data"]
    assert len(items) == 1 and items[0]["active"] is True

    # 写操作：非管理员 403
    assert (
        client.put(
            f"/api/settings/learned-rules/{rule['id']}",
            json={"enabled": False},
            headers=A_HEADERS,
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/settings/learned-rules/{rule['id']}", headers=A_HEADERS
        ).status_code
        == 403
    )

    # 管理员编辑：停用生效，active 随之变化
    resp = client.put(
        f"/api/settings/learned-rules/{rule['id']}",
        json={"enabled": False},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["active"] is False

    # 管理员改目标分类
    resp = client.put(
        f"/api/settings/learned-rules/{rule['id']}",
        json={"category": "购物"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["category"] == "购物"

    # 管理员删除
    assert (
        client.delete(
            f"/api/settings/learned-rules/{rule['id']}", headers=ADMIN_HEADERS
        ).json()["data"]["ok"]
        is True
    )
    assert (
        client.get("/api/settings/learned-rules", headers=A_HEADERS).json()["data"]
        == []
    )


def test_api_rule_list_sorted_by_hits(client, db):
    _correct_twice("瑞幸咖啡", "餐饮")
    _correct_twice("瑞幸咖啡", "购物")
    bill_id = _seed_one("瑞幸咖啡")
    bill_service.update_bill(
        bill_id, BillUpdate(category="购物"), USER_A
    )  # 购物 hits=3
    items = client.get("/api/settings/learned-rules", headers=A_HEADERS).json()["data"]
    assert [i["hits"] for i in items] == [3, 2]
