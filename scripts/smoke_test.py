"""API 冒烟测试（纯标准库，无第三方依赖）

用法：
    python scripts/gen_sample_bills.py   # 先生成样例账单
    python scripts/smoke_test.py [base_url]

验证：首页、分类、导入（微信/支付宝）、流水 CRUD、筛选分页、统计报表、异常分支。
"""
import json
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8090").rstrip("/")
ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / ".local_tmp"

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {extra}")


def call(method: str, path: str, payload=None):
    # 中文查询参数自动 URL 编码
    path = urllib.parse.quote(path, safe="/?=&")
    url = BASE + path
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            if not body:
                return resp.status, None
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            detail = json.loads(body).get("detail", body[:200])
        except Exception:
            detail = body[:200]
        return e.code, detail


def upload(path: str, file_path: Path):
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + file_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + path,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()).get("detail")


def main():
    print(f"=== fn-finstat 冒烟测试 @ {BASE} ===")

    # 首页与静态资源
    code, html = call("GET", "/")
    check("GET / 返回首页", code == 200 and "财务统计" in str(html))

    # 分类：默认 7 个预置（重复运行不影响）
    code, cats = call("GET", "/api/category")
    default_names = {"餐饮", "交通", "购物", "住房", "医疗", "娱乐", "其他"}
    check(
        "GET /api/category 预置分类存在",
        code == 200 and len(cats) >= 7 and default_names.issubset({c["name"] for c in cats}),
        str(cats)[:120],
    )

    # 新增分类（唯一名，支持重复运行）+ 重复拦截
    cat_name = f"数码{int(time.time()) % 100000}"
    code, new_cat = call("POST", "/api/category", {"name": cat_name})
    check("POST /api/category 新增", code == 201 and new_cat["name"] == cat_name)
    code, _ = call("POST", "/api/category", {"name": cat_name})
    check("POST /api/category 重复返回400", code == 400)

    # 微信账单导入（10条）+ 重复导入去重（幂等：已存在则全跳过）
    wx_file = TMP / "wechat_sample.xlsx"
    code, res = upload("/api/upload/wechat", wx_file)
    check("POST /api/upload/wechat 导入10条", code == 200 and res["total"] == 10 and res["inserted"] + res["skipped"] == 10, str(res))
    code, res = upload("/api/upload/wechat", wx_file)
    check("POST /api/upload/wechat 重复导入全跳过", code == 200 and res["inserted"] == 0 and res["skipped"] == 10, str(res))

    # 支付宝账单导入（8条，幂等）
    al_file = TMP / "alipay_sample.csv"
    code, res = upload("/api/upload/alipay", al_file)
    check("POST /api/upload/alipay 导入8条", code == 200 and res["total"] == 8 and res["inserted"] + res["skipped"] == 8, str(res))

    # 错误后缀拦截
    code, _ = upload("/api/upload/wechat", al_file)
    check("POST /api/upload/wechat 错误后缀返回400", code == 400)

    # 流水分页/筛选
    code, page = call("GET", "/api/bill/list?page=1&page_size=5")
    check("GET /api/bill/list 分页", code == 200 and page["total"] == 18 and len(page["items"]) == 5, str(page)[:160])
    code, page = call("GET", "/api/bill/list?tx_type=expense")
    check("GET /api/bill/list 支出筛选", code == 200 and all(i["tx_type"] == "expense" for i in page["items"]))
    code, page = call("GET", "/api/bill/list?category=餐饮")
    check("GET /api/bill/list 分类筛选", code == 200 and all(i["category"] == "餐饮" for i in page["items"]))
    code, page = call("GET", "/api/bill/list?start=2026-08-01&end=2026-08-31")
    check("GET /api/bill/list 时间范围", code == 200 and all("2026-08-" in i["tx_time"] for i in page["items"]))

    # 表头排序：金额升序 / 时间降序默认 / 非法字段拦截
    code, page = call("GET", "/api/bill/list?sort_by=amount&order=asc")
    amounts = [i["amount"] for i in page["items"]]
    check("GET /api/bill/list 金额升序", code == 200 and len(amounts) >= 2 and amounts == sorted(amounts), str(amounts)[:120])
    code, page = call("GET", "/api/bill/list?sort_by=tx_time&order=desc")
    times = [i["tx_time"] for i in page["items"]]
    check("GET /api/bill/list 时间降序", code == 200 and times == sorted(times, reverse=True), str(times)[:120])
    code, _ = call("GET", "/api/bill/list?sort_by=id;--")
    check("GET /api/bill/list 非法排序字段返回400", code == 400)

    # 自动归类抽查：微信海底捞->餐饮、京东->购物；支付宝滴滴->交通
    code, page = call("GET", "/api/bill/list?category=餐饮")
    names = [i["merchant"] for i in page["items"]]
    check("关键词自动归类(餐饮)", "海底捞火锅(春熙路店)" in names and "瑞幸咖啡" in names)
    code, page = call("GET", "/api/bill/list?category=交通")
    names = [i["merchant"] for i in page["items"]]
    check("关键词自动归类(交通)", "滴滴出行" in names)

    # 手动新增（唯一流水号，支持重复运行）
    manual_txid = f"MANUAL{int(time.time() * 1000)}"
    payload = {
        "tx_time": "2026-09-08 12:00:00",
        "account": "wechat",
        "tx_type": "expense",
        "merchant": "测试商户",
        "amount": 12.5,
        "category": "数码",
        "tx_id": manual_txid,
        "remark": "冒烟测试",
    }
    code, bill = call("POST", "/api/bill", payload)
    check("POST /api/bill 新增", code == 201 and bill["amount"] == 12.5, str(bill)[:160])
    bid = bill["id"]

    # 编辑
    code, bill = call("PUT", f"/api/bill/{bid}", {"merchant": "测试商户改", "category": "餐饮"})
    check("PUT /api/bill 编辑", code == 200 and bill["merchant"] == "测试商户改" and bill["category"] == "餐饮")
    code, bill = call("GET", f"/api/bill/{bid}")
    check("GET /api/bill/{id}", code == 200 and bill["id"] == bid)

    # 交易号冲突
    code, _ = call("POST", "/api/bill", {**payload, "tx_id": manual_txid})
    check("POST /api/bill 交易号冲突返回400", code == 400)

    # 删除
    code, _ = call("DELETE", f"/api/bill/{bid}")
    check("DELETE /api/bill 删除", code == 204)
    code, _ = call("DELETE", f"/api/bill/{bid}")
    check("DELETE /api/bill 重复删除返回404", code == 404)

    # 统计报表
    code, s = call("GET", "/api/stat/summary")
    check("GET /api/stat/summary 收支汇总", code == 200 and s["income"] > 0 and s["expense"] > 0 and abs(s["net"] - (s["income"] - s["expense"])) < 0.01, str(s))
    code, trend = call("GET", "/api/stat/month_trend")
    check("GET /api/stat/month_trend 月度趋势", code == 200 and len(trend) >= 2, str(trend)[:120])
    code, pie = call("GET", "/api/stat/category_pie")
    check("GET /api/stat/category_pie 分类饼图", code == 200 and len(pie) >= 5, str(pie)[:120])
    code, top = call("GET", "/api/stat/merchant_top?limit=5")
    check("GET /api/stat/merchant_top 商户TOP", code == 200 and len(top) <= 5 and top and top[0]["amount"] >= top[-1]["amount"], str(top)[:160])
    code, s2 = call("GET", "/api/stat/summary?account=alipay")
    check("统计按账户筛选", code == 200)

    print(f"\n=== 结果：通过 {PASS} 项 / 失败 {FAIL} 项 ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
