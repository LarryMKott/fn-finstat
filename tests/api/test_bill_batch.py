"""流水批量操作、回收站与标签/报销功能测试（API 级，覆盖账号隔离）"""

from tests.conftest import USER_A, USER_B, make_bill_records

A_HEADERS = {"X-Trim-Userid": USER_A}
B_HEADERS = {"X-Trim-Userid": USER_B}


def seed(client, count=3, prefix="BX", user_headers=A_HEADERS, **overrides):
    """经 API 直接入库若干条（借 DAO 更直接，避免依赖上传）"""
    from app.db.dao.bill_dao import BillDAO

    user_id = user_headers["X-Trim-Userid"]
    return BillDAO.insert_many(
        make_bill_records(count, prefix=prefix, **overrides), user_id
    )


def get_ids(client, headers=A_HEADERS):
    res = client.get("/api/bill/list?page_size=100", headers=headers).json()["data"]
    return [b["id"] for b in res["items"]]


# ---- 批量操作 ----


def test_batch_set_category_and_tags(client):
    seed(client, 2, prefix="BA")
    ids = get_ids(client)
    res = client.post(
        "/api/bill/batch",
        headers=A_HEADERS,
        json={"ids": ids, "action": "set_category", "category": "餐饮"},
    )
    assert res.status_code == 200 and res.json()["data"]["updated"] == 2
    res = client.post(
        "/api/bill/batch",
        headers=A_HEADERS,
        json={"ids": ids, "action": "set_tags", "tags": "出差, 杭州 ,出差"},
    )
    assert res.json()["data"]["updated"] == 2
    rows = client.get("/api/bill/list?page_size=10", headers=A_HEADERS).json()["data"][
        "items"
    ]
    assert all(r["category"] == "餐饮" for r in rows)
    assert all(r["tags"] == "出差,杭州" for r in rows)  # 归一化去重去空


def test_batch_set_reimbursed(client):
    seed(client, 2, prefix="BR")
    ids = get_ids(client)
    res = client.post(
        "/api/bill/batch",
        headers=A_HEADERS,
        json={"ids": ids, "action": "set_reimbursed", "reimbursed": True},
    )
    assert res.json()["data"]["updated"] == 2
    rows = client.get("/api/bill/list?reimbursed=true", headers=A_HEADERS).json()[
        "data"
    ]
    assert rows["total"] == 2
    rows = client.get("/api/bill/list?reimbursed=false", headers=A_HEADERS).json()[
        "data"
    ]
    assert rows["total"] == 0


def test_batch_validation(client):
    res = client.post(
        "/api/bill/batch", headers=A_HEADERS, json={"ids": [], "action": "delete"}
    )
    assert res.status_code == 422  # ids 最少 1 条
    res = client.post(
        "/api/bill/batch",
        headers=A_HEADERS,
        json={"ids": [1], "action": "set_category", "category": ""},
    )
    assert res.status_code == 400
    res = client.post(
        "/api/bill/batch", headers=A_HEADERS, json={"ids": [1], "action": "nope"}
    )
    assert res.status_code == 422


# ---- 回收站 ----


def test_soft_delete_recycle_and_restore(client):
    seed(client, 3, prefix="RD")
    ids = get_ids(client)

    # 删除两条 → 进回收站
    assert client.delete(f"/api/bill/{ids[0]}", headers=A_HEADERS).status_code == 204
    assert client.delete(f"/api/bill/{ids[1]}", headers=A_HEADERS).status_code == 204
    assert (
        client.get("/api/bill/list?page_size=10", headers=A_HEADERS).json()["data"][
            "total"
        ]
        == 1
    )

    # 重复删除已删除的流水 → 404
    assert client.delete(f"/api/bill/{ids[0]}", headers=A_HEADERS).status_code == 404

    # 回收站列表
    recycle = client.get("/api/bill/recycle", headers=A_HEADERS).json()["data"]
    assert recycle["total"] == 2
    assert {b["id"] for b in recycle["items"]} == {ids[0], ids[1]}

    # 还原一条
    res = client.post(
        "/api/bill/recycle/restore", headers=A_HEADERS, json={"ids": [ids[0]]}
    )
    assert res.json()["data"]["updated"] == 1
    assert (
        client.get("/api/bill/list?page_size=10", headers=A_HEADERS).json()["data"][
            "total"
        ]
        == 2
    )

    # 彻底删除另一条（TestClient.delete 不带 json 体，用 request）
    res = client.request(
        "DELETE", "/api/bill/recycle", headers=A_HEADERS, json={"ids": [ids[1]]}
    )
    assert res.json()["data"]["updated"] == 1
    assert (
        client.get("/api/bill/recycle", headers=A_HEADERS).json()["data"]["total"] == 0
    )
    assert (
        client.get("/api/bill/list?page_size=10", headers=A_HEADERS).json()["data"][
            "total"
        ]
        == 2
    )


def test_empty_recycle(client):
    seed(client, 2, prefix="RE")
    for bill_id in get_ids(client):
        client.delete(f"/api/bill/{bill_id}", headers=A_HEADERS)
    res = client.post("/api/bill/recycle/empty", headers=A_HEADERS)
    assert res.json()["data"]["updated"] == 2
    assert (
        client.get("/api/bill/recycle", headers=A_HEADERS).json()["data"]["total"] == 0
    )


def test_recycle_scoped_by_user(client):
    seed(client, 1, prefix="RU", user_headers=A_HEADERS)
    seed(client, 1, prefix="RV", user_headers=B_HEADERS)
    a_id = get_ids(client, A_HEADERS)[0]
    b_id = get_ids(client, B_HEADERS)[0]

    client.delete(f"/api/bill/{a_id}", headers=A_HEADERS)
    # B 看不到 A 的回收站
    assert (
        client.get("/api/bill/recycle", headers=B_HEADERS).json()["data"]["total"] == 0
    )
    # B 不能还原/彻底删除 A 的流水
    assert (
        client.post(
            "/api/bill/recycle/restore", headers=B_HEADERS, json={"ids": [a_id]}
        ).json()["data"]["updated"]
        == 0
    )
    assert (
        client.request(
            "DELETE", "/api/bill/recycle", headers=B_HEADERS, json={"ids": [a_id]}
        ).json()["data"]["updated"]
        == 0
    )
    # A 自己也动不了 B 的流水
    assert (
        client.request(
            "DELETE", "/api/bill/recycle", headers=A_HEADERS, json={"ids": [b_id]}
        ).json()["data"]["updated"]
        == 0
    )


# ---- 标签/报销筛选与单条编辑 ----


def test_tag_filter_is_exact_match(client):
    from app.db.dao.bill_dao import BillDAO

    BillDAO.insert_many(
        [
            make_bill_records(1, prefix="TG1")[0] | {"tags": "出差"},
            make_bill_records(1, prefix="TG2")[0] | {"tags": "出差报销"},
        ],
        USER_A,
    )
    rows = client.get("/api/bill/list?tag=出差", headers=A_HEADERS).json()["data"]
    assert rows["total"] == 1  # 子串「出差报销」不误命中
    assert rows["items"][0]["tags"] == "出差"


def test_create_and_update_with_tags_reimbursed(client):
    payload = {
        "tx_time": "2026-09-01 12:00:00",
        "account": "jd",
        "tx_type": "expense",
        "merchant": "京东",
        "amount": 99.9,
        "category": "购物",
        "tags": "自营, 双十一",
        "reimbursed": True,
    }
    res = client.post("/api/bill", headers=A_HEADERS, json=payload)
    assert res.status_code == 201
    body = res.json()["data"]
    assert body["tags"] == "自营,双十一"
    assert body["reimbursed"] is True

    res = client.put(
        f"/api/bill/{body['id']}",
        headers=A_HEADERS,
        json={"tags": "", "reimbursed": False},
    )
    assert res.json()["data"]["tags"] == ""
    assert res.json()["data"]["reimbursed"] is False
