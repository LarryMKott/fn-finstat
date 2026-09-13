"""账单流水服务层测试（校验、归一化、去重与账号隔离）"""

import pytest

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.schemas.bill import BillCreate, BillUpdate
from app.services import bill_service
from tests.conftest import USER_A, USER_B


def create(**overrides) -> dict:
    payload = {
        "tx_time": "2024-01-01 12:00:00",
        "account": "wechat",
        "tx_type": "expense",
        "merchant": "测试",
        "amount": 10.0,
        "category": "",
        "tx_id": "",
        "remark": "",
    }
    payload.update(overrides)
    return bill_service.create_bill(BillCreate(**payload), USER_A)


def test_create_normalizes_empty_category_to_default(db):
    bill = create()
    assert bill["category"] == "其他"
    assert bill["user_id"] == USER_A
    assert bill["tx_id"] is None  # 空交易号归一化为 NULL


def test_create_ensures_new_category(db):
    bill = create(category="自定义分类")
    assert bill["category"] == "自定义分类"
    from app.db.dao.category_dao import CategoryDAO

    assert CategoryDAO.get_by_name("自定义分类") is not None


@pytest.mark.parametrize("tx_type", ["expense", "income", "transfer"])
def test_create_accepts_all_types(db, tx_type):
    assert create(tx_type=tx_type)["tx_type"] == tx_type


def test_create_rejects_duplicate_tx_id(db):
    create(tx_id="TX-1")
    with pytest.raises(ConflictError) as e:  # 预检查命中 → ConflictError（HTTP 400）
        create(tx_id="TX-1")
    assert e.value.http_status == 400


def make_raw(**overrides) -> BillCreate:
    """绕过 pydantic 校验构造模型，验证服务层自身的入参兜底"""
    payload = {
        "tx_time": "2024-01-01 12:00:00",
        "account": "wechat",
        "tx_type": "expense",
        "merchant": "",
        "amount": 1.0,
        "category": "",
        "tx_id": "",
        "remark": "",
    }
    payload.update(overrides)
    return BillCreate.model_construct(**payload)


def test_service_layer_validation_backstop(db):
    """pydantic 拦不住的非法值由服务层二次校验兜底"""
    for bad in (
        {"tx_type": "unknown"},
        {"account": "bank"},
        {"amount": 0},
        {"amount": -5},
    ):
        with pytest.raises(ValidationError) as e:
            bill_service.create_bill(make_raw(**bad), USER_A)
        assert e.value.http_status == 400


def test_list_bills_rejects_bad_sort_params(db):
    with pytest.raises(ValidationError):
        bill_service.list_bills(USER_A, {}, 1, 20, sort_by="password; drop")
    with pytest.raises(ValidationError):
        bill_service.list_bills(USER_A, {}, 1, 20, order="sideways")


def test_get_bill_404_for_other_user(db):
    bill = create()
    assert bill_service.get_bill(bill["id"], USER_A)["id"] == bill["id"]
    with pytest.raises(NotFoundError) as e:
        bill_service.get_bill(bill["id"], USER_B)
    assert e.value.http_status == 404


def test_update_partial_fields(db):
    bill = create(tx_id="TX-U")
    updated = bill_service.update_bill(
        bill["id"], BillUpdate(amount=99.999, remark="改"), USER_A
    )
    assert updated["amount"] == 100.0  # 归一化到 2 位小数
    assert updated["remark"] == "改"
    assert updated["tx_time"] == bill["tx_time"]  # 未传字段不变


def test_update_empty_tx_id_becomes_null(db):
    bill = create(tx_id="TX-U2")
    updated = bill_service.update_bill(bill["id"], BillUpdate(tx_id=""), USER_A)
    assert updated["tx_id"] is None


def test_update_validations(db):
    bill = create()
    other = create(tx_id="OTHER")  # 供 tx_id 冲突用
    with pytest.raises(NotFoundError):  # 404：别人的账单
        bill_service.update_bill(other["id"], BillUpdate(remark="x"), USER_B)
    with pytest.raises(ValidationError):  # 非法类型
        bill_service.update_bill(
            bill["id"], BillUpdate.model_construct(tx_type="bad"), USER_A
        )
    with pytest.raises(ValidationError):  # 非法账户
        bill_service.update_bill(
            bill["id"], BillUpdate.model_construct(account="bank"), USER_A
        )
    with pytest.raises(ValidationError):  # 金额必须大于 0
        bill_service.update_bill(
            bill["id"], BillUpdate.model_construct(amount=-1), USER_A
        )
    with pytest.raises(ConflictError):  # tx_id 与他人冲突
        bill_service.update_bill(bill["id"], BillUpdate(tx_id="OTHER"), USER_A)
    with pytest.raises(NotFoundError):  # 不存在的账单
        bill_service.update_bill(99999, BillUpdate(remark="x"), USER_A)


def test_update_tx_id_unchanged_allows_self(db):
    bill = create(tx_id="KEEP")
    updated = bill_service.update_bill(bill["id"], BillUpdate(remark="x"), USER_A)
    assert updated["tx_id"] == "KEEP"


def test_delete_scoped_and_404(db):
    bill = create()
    with pytest.raises(NotFoundError):
        bill_service.delete_bill(bill["id"], USER_B)
    bill_service.delete_bill(bill["id"], USER_A)
    with pytest.raises(NotFoundError):
        bill_service.delete_bill(bill["id"], USER_A)
