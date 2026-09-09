"""分类自动创建 专项验证（标准库实现，可重复运行）

场景：categories 表缺失分类（模拟自定义/历史数据不一致），导入账单时
      - 新规则分类“数码”自动创建（小米商城 → 数码）
      - 原有分类“购物”自动补回（京东商城 → 购物，规则“京东”先命中）

用法：
    python scripts/verify_auto_category.py [base_url]
"""
import json
import sqlite3
import sys
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8090").rstrip("/")
ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / ".local_data" / "finance" / "bill.db"
TMP = ROOT / ".local_tmp"
XLSX = TMP / "verify_auto_category.xlsx"

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


def call(method, path, payload=None):
    path = urllib.parse.quote(path, safe="/?=&")
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            detail = json.loads(body).get("detail", body[:200])
        except Exception:
            detail = body[:200]
        return e.code, detail


def upload(file_path: Path):
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + file_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + "/api/upload/wechat",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()).get("detail")


def gen_xlsx():
    """生成含 小米商城/京东商城 两条流水的最小微信账单 xlsx（交易号 VF 前缀避免与样例冲突）"""
    from openpyxl import Workbook

    header = ["交易时间", "交易类型", "交易对方", "商品", "收/支", "金额(元)",
              "支付方式", "当前状态", "交易单号", "商户单号", "备注"]
    rows = [
        ("2026-09-10 10:00:00", "商户消费", "小米商城", "数码产品", "支出", "¥2999.00", "零钱", "支付成功", "VF1001", "", ""),
        ("2026-09-10 11:00:00", "商户消费", "京东商城", "数码产品", "支出", "¥199.00", "零钱", "支付成功", "VF1002", "", ""),
    ]
    wb = Workbook()
    ws = wb.active
    ws.title = "微信支付账单明细"
    ws.append(["微信支付账单明细"])
    ws.append(["微信昵称：[验证用户]"])
    ws.append(["起始时间：[2026-09-01 00:00:00] 终止时间：[2026-09-30 23:59:59]"])
    ws.append(["导出类型：全部"])
    ws.append(["----------------------微信支付账单明细---------------------"])
    ws.append(header)
    for r in rows:
        ws.append(list(r))
    ws.append([f"共{len(rows)}笔"])
    wb.save(XLSX)
    print(f"  generated: {XLSX}")


def main():
    print(f"=== 分类自动创建 专项验证 @ {BASE} ===")

    # 0. 前置：删除 categories 表中“购物”“数码”，模拟分类缺失
    if not DB.exists():
        print("  [SKIP] 数据库不存在，请先导入样例数据再运行")
        sys.exit(0)
    with sqlite3.connect(DB) as conn:
        cur = conn.execute("DELETE FROM categories WHERE name IN ('购物','数码')")
        print(f"  前置：删除缺失分类 {cur.rowcount} 行")

    gen_xlsx()

    # 1. 上传 → 期望 2 条入库（交易号 VF 唯一，可重复运行）
    code, res = upload(XLSX)
    check("上传2条账单", code == 200 and res["total"] == 2 and res["inserted"] == 2, str(res))

    # 2. 分类自动创建：购物 / 数码 均已恢复
    code, cats = call("GET", "/api/category")
    names = {c["name"] for c in cats}
    check("分类自动创建(购物补回)", code == 200 and "购物" in names, str(names))
    check("分类自动创建(数码新增)", code == 200 and "数码" in names, str(names))

    # 3. 自动归类结果：小米商城→数码（新规则），京东商城→购物（原有规则优先）
    code, page = call("GET", "/api/bill/list?category=数码")
    digi_names = {i["merchant"] for i in page["items"]}
    check("小米商城→数码", code == 200 and "小米商城" in digi_names, str(digi_names))
    code, page = call("GET", "/api/bill/list?category=购物")
    shop_names = {i["merchant"] for i in page["items"]}
    check("京东商城→购物", code == 200 and "京东商城" in shop_names, str(shop_names))

    # 4. 清理：删除验证产生的流水（09-10 为验证专用日期，样例数据无该日记录；分类保留）
    #    注：日期筛选为字符串比较，end 需传次日才能覆盖当天含时间戳的记录
    code, page = call("GET", "/api/bill/list?start=2026-09-10&end=2026-09-11")
    deleted = 0
    for item in (page or {}).get("items", []):
        call("DELETE", f"/api/bill/{item['id']}")
        deleted += 1
    print(f"  清理完成，删除 {deleted} 条验证流水")
    XLSX.unlink(missing_ok=True)
    print("  清理完成")

    print(f"\n=== 结果：通过 {PASS} 项 / 失败 {FAIL} 项 ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
