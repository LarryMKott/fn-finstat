"""账单流水 DAO 测试（数据按 user_id 归属账号）"""

from tests.conftest import USER_A, USER_B, make_bill_records
from app.db.dao.bill_dao import BillDAO, SORTABLE_FIELDS


def test_insert_many_assigns_user_id_and_dedupes_tx_id(db):
    records = make_bill_records(3)
    assert BillDAO.insert_many(records, USER_A) == 3
    # 重复导入：全部按 tx_id 去重跳过
    assert BillDAO.insert_many(records, USER_A) == 0
    # 同 tx_id 归属另一账号也被全局唯一约束拦截
    assert BillDAO.insert_many(make_bill_records(1, tx_id="TX-0000"), USER_B) == 0
    total, rows = BillDAO.list_bills(USER_A)
    assert total == 3
    assert all(r["user_id"] == USER_A for r in rows)


def test_insert_many_empty_tx_id_allows_multiple(db):
    """空交易号归一化为 NULL，UNIQUE 约束允许多个 NULL"""
    records = make_bill_records(3, tx_id="")
    assert BillDAO.insert_many(records, USER_A) == 3
    assert BillDAO.insert_many(records, USER_A) == 3  # NULL 之间互不冲突
    total, _ = BillDAO.list_bills(USER_A)
    assert total == 6


def test_insert_many_empty_list(db):
    assert BillDAO.insert_many([], USER_A) == 0


def test_list_bills_filters(db):
    BillDAO.insert_many(
        make_bill_records(
            2, tx_time="2024-01-05 10:00:00", category="餐饮", account="wechat"
        ),
        USER_A,
    )
    BillDAO.insert_many(
        make_bill_records(
            1,
            prefix="TB",
            tx_time="2024-02-10 10:00:00",
            category="交通",
            account="alipay",
            tx_type="income",
        ),
        USER_A,
    )
    BillDAO.insert_many(
        make_bill_records(
            1,
            prefix="TC",
            tx_time="2024-02-20 10:00:00",
            category="交通",
            account="wechat",
            tx_id="",
        ),
        USER_A,
    )
    BillDAO.insert_many(
        make_bill_records(5, prefix="TD", tx_time="2024-02-01 10:00:00"), USER_B
    )

    assert BillDAO.list_bills(USER_A, start="2024-02-01", end="2024-02-28")[0] == 2
    assert BillDAO.list_bills(USER_A, category="餐饮")[0] == 2
    assert BillDAO.list_bills(USER_A, account="alipay")[0] == 1
    assert BillDAO.list_bills(USER_A, tx_type="income")[0] == 1
    # 账号隔离：B 的 5 条对 A 不可见
    assert BillDAO.list_bills(USER_A)[0] == 4
    assert BillDAO.list_bills(USER_B)[0] == 5


def test_list_bills_pagination_and_sort(db):
    records = [
        {
            "tx_time": f"2024-01-{day:02d} 10:00:00",
            "account": "wechat",
            "tx_type": "expense",
            "merchant": f"商户{day}",
            "amount": float(day),
            "category": "其他",
            "tx_id": f"PG-{day:02d}",
            "remark": "",
        }
        for day in range(1, 6)
    ]
    BillDAO.insert_many(records, USER_A)
    total, page1 = BillDAO.list_bills(
        USER_A, page=1, page_size=2, sort_by="tx_time", order="asc"
    )
    assert (total, len(page1)) == (5, 2)
    assert page1[0]["tx_time"] == "2024-01-01 10:00:00"

    _, page3 = BillDAO.list_bills(
        USER_A, page=3, page_size=2, sort_by="tx_time", order="asc"
    )
    assert len(page3) == 1

    _, by_amount = BillDAO.list_bills(USER_A, sort_by="amount", order="desc")
    assert by_amount[0]["amount"] == 5.0


def test_get_by_id_scoped_by_user(db):
    BillDAO.insert_many(make_bill_records(1), USER_A)
    bill_id = BillDAO.list_bills(USER_A)[1][0]["id"]
    assert BillDAO.get_by_id(bill_id, USER_A)["id"] == bill_id
    assert BillDAO.get_by_id(bill_id, USER_B) is None
    assert BillDAO.get_by_id(99999, USER_A) is None


def test_tx_id_exists_with_exclude(db):
    BillDAO.insert_many(make_bill_records(1, tx_id="TX-1"), USER_A)
    bill_id = BillDAO.list_bills(USER_A)[1][0]["id"]
    assert BillDAO.tx_id_exists("TX-1") is True
    assert BillDAO.tx_id_exists("TX-1", exclude_id=bill_id) is False
    assert BillDAO.tx_id_exists("nope") is False


def test_update_scoped_by_user(db):
    BillDAO.insert_many(make_bill_records(1), USER_A)
    bill_id = BillDAO.list_bills(USER_A)[1][0]["id"]
    assert BillDAO.update(bill_id, {"remark": "改过"}, USER_B) is False
    assert BillDAO.update(bill_id, {"remark": "改过"}, USER_A) is True
    assert BillDAO.get_by_id(bill_id, USER_A)["remark"] == "改过"
    assert BillDAO.update(bill_id, {}, USER_A) is False  # 空字段不执行


def test_delete_scoped_by_user(db):
    BillDAO.insert_many(make_bill_records(1), USER_A)
    bill_id = BillDAO.list_bills(USER_A)[1][0]["id"]
    assert BillDAO.delete(bill_id, USER_B) is False
    assert BillDAO.delete(bill_id, USER_A) is True
    assert BillDAO.get_by_id(bill_id, USER_A) is None


def test_count_unassigned_and_claim(db):
    BillDAO.insert_many(make_bill_records(2, prefix="T0"), "")  # 历史无归属数据
    BillDAO.insert_many(make_bill_records(1, prefix="TB"), USER_B)
    assert BillDAO.count_unassigned() == 2
    assert BillDAO.claim_unassigned("") == 0  # 空账号不认领
    assert BillDAO.claim_unassigned(USER_A) == 2
    assert BillDAO.count_unassigned() == 0
    total, _ = BillDAO.list_bills(USER_A)
    assert total == 2


def test_sortable_fields_exclude_id_and_user_id():
    assert "id" not in SORTABLE_FIELDS
    assert "user_id" not in SORTABLE_FIELDS
    assert "amount" in SORTABLE_FIELDS
