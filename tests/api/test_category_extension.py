"""分类扩展 API 测试：分类树、层级创建、删除保护、关键词 CRUD 与权限

权限口径：读公开、写（分类与关键词）管理员；非管理员写 403（与项目既有
断言口径一致）。
"""

from app.db.dao.bill_dao import BillDAO
from app.db.dao.category_dao import CategoryDAO
from tests.conftest import USER_A, make_bill_records

A_HEADERS = {
    "X-Trim-Userid": USER_A,
    "X-Trim-Username": "zhangsan",
    "X-Trim-Isadmin": "true",
}
B_HEADERS = {"X-Trim-Userid": "10002", "X-Trim-Username": "lisi"}


def _food_id():
    return CategoryDAO.get_by_name("餐饮")["id"]


def test_tree_rollup_includes_children(client, db):
    client.post(
        "/api/category",
        json={"name": "喵喵烘焙", "parent_id": _food_id()},
        headers=A_HEADERS,
    )
    BillDAO.insert_many(
        make_bill_records(3, merchant="喵喵烘焙坊", category="喵喵烘焙"), USER_A
    )
    BillDAO.insert_many(
        make_bill_records(1, prefix="T9", merchant="老王炒菜", category="餐饮"),
        USER_A,
    )
    resp = client.get("/api/category/tree", headers=A_HEADERS)
    assert resp.status_code == 200
    tree = resp.json()["data"]
    food = next(n for n in tree if n["name"] == "餐饮")
    child = next(n for n in food["children"] if n["name"] == "喵喵烘焙")
    assert child["bill_count"] == 3
    assert food["bill_count"] == 4  # rollup 含子孙
    assert abs(food["expense_total"] - 40.0) < 0.01  # 3×10 + 1×10


def test_flat_list_carries_children_and_parent(client, db):
    client.post(
        "/api/category",
        json={"name": "喵喵烘焙", "parent_id": _food_id()},
        headers=A_HEADERS,
    )
    cats = client.get("/api/category", headers=A_HEADERS).json()["data"]
    food = next(c for c in cats if c["name"] == "餐饮")
    child = next(c for c in cats if c["name"] == "喵喵烘焙")
    assert child["parent_id"] == food["id"]
    assert child["id"] in food["children"]


def test_create_subcategory_requires_admin(client, db):
    resp = client.post(
        "/api/category",
        json={"name": "喵喵烘焙", "parent_id": _food_id()},
        headers=B_HEADERS,
    )
    assert resp.status_code in (401, 403)


def test_create_child_of_child_rejected(client, db):
    client.post(
        "/api/category",
        json={"name": "喵喵烘焙", "parent_id": _food_id()},
        headers=A_HEADERS,
    )
    child_id = CategoryDAO.get_by_name("喵喵烘焙")["id"]
    resp = client.post(
        "/api/category",
        json={"name": "再下一层", "parent_id": child_id},
        headers=A_HEADERS,
    )
    assert resp.status_code == 400


def test_delete_parent_with_children_rejected(client, db):
    client.post(
        "/api/category",
        json={"name": "喵喵烘焙", "parent_id": _food_id()},
        headers=A_HEADERS,
    )
    resp = client.delete(f"/api/category/{_food_id()}", headers=A_HEADERS)
    assert resp.status_code == 400
    assert CategoryDAO.get_by_name("餐饮") is not None


def test_delete_child_moves_its_bills(client, db):
    client.post(
        "/api/category",
        json={"name": "喵喵烘焙", "parent_id": _food_id()},
        headers=A_HEADERS,
    )
    BillDAO.insert_many(
        make_bill_records(2, merchant="喵喵烘焙坊", category="喵喵烘焙"), USER_A
    )
    child_id = CategoryDAO.get_by_name("喵喵烘焙")["id"]
    resp = client.delete(f"/api/category/{child_id}", headers=A_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["data"]["moved_bills"] == 2


# ---- 关键词接口 ----


def test_keyword_list_public_add_admin_only(client, db):
    cat_id = _food_id()
    assert client.get(f"/api/category/{cat_id}/keywords").status_code == 200

    denied = client.post(
        f"/api/category/{cat_id}/keywords",
        json={"keywords": ["喵喵"]},
        headers=B_HEADERS,
    )
    assert denied.status_code in (401, 403)

    ok = client.post(
        f"/api/category/{cat_id}/keywords",
        json={"keywords": ["喵喵", "喵喵包", "a"]},
        headers=A_HEADERS,
    )
    assert ok.status_code == 200
    body = ok.json()["data"]
    assert body["added"] == 2
    assert body["dropped"] == 1  # 「a」过短

    rows = client.get(f"/api/category/{cat_id}/keywords").json()["data"]
    assert {r["keyword"] for r in rows} >= {"喵喵", "喵喵包"}


def test_keyword_toggle_and_delete_admin_only(client, db):
    cat_id = _food_id()
    client.post(
        f"/api/category/{cat_id}/keywords",
        json={"keywords": ["喵喵"]},
        headers=A_HEADERS,
    )
    kw = next(
        r
        for r in client.get(f"/api/category/{cat_id}/keywords").json()["data"]
        if r["keyword"] == "喵喵"
    )

    denied = client.put(
        f"/api/category/keyword/{kw['id']}", json={"enabled": False}, headers=B_HEADERS
    )
    assert denied.status_code in (401, 403)

    toggled = client.put(
        f"/api/category/keyword/{kw['id']}", json={"enabled": False}, headers=A_HEADERS
    )
    assert toggled.status_code == 200
    assert toggled.json()["data"]["enabled"] is False

    deleted = client.delete(f"/api/category/keyword/{kw['id']}", headers=A_HEADERS)
    assert deleted.status_code == 200
    assert deleted.json()["data"]["ok"] is True


def test_keyword_list_unknown_category_404(client, db):
    resp = client.get("/api/category/99999/keywords")
    assert resp.status_code == 404


# ---- AI 生成入口权限（管理员）----


def test_ai_generate_endpoints_admin_only(client, db):
    for path, payload in (
        ("/api/ai/category/keywords", {"category_id": _food_id()}),
        (
            "/api/ai/category/keywords/apply",
            {"category_id": _food_id(), "keywords": ["喵喵"]},
        ),
        ("/api/ai/category/children", {"category_id": _food_id()}),
        (
            "/api/ai/category/children/apply",
            {
                "category_id": _food_id(),
                "children": [{"name": "喵喵烘焙", "keywords": ["喵喵包"]}],
            },
        ),
    ):
        resp = client.post(path, json=payload, headers=B_HEADERS)
        assert resp.status_code in (401, 403), path
