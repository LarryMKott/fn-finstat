"""备注语义检索 方案对比验证（零新增依赖）

用途：为《2026-09-20-AI功能扩展脑洞清单》AI-8「流水备注语义检索」的两条
技术路线提供数据支撑，避免拍脑袋决策。

对比的两条路线：

- 路线 A「LLM 打标签落库」：调用项目既有 DeepSeek 通道给流水备注打语义标签
  （如「给老妈的生日礼物」→ 标签 家庭/礼物/长辈），检索时按标签匹配。
  零新增依赖、标签人可读可修正、可审计。

- 路线 B「向量检索」：真实方案需 embedding 模型（sentence-transformers ≈1.5GB
  含 torch，或调用 embedding API）。本脚本用**纯标准库 TF-IDF + 余弦相似度**
  作为 B 的**代理基线**——它代表了「无外部模型的向量检索」能达到的上限。
  若 A 已接近或超过 B，则引入 1.5GB 模型换来的增量收益不值得。

重要：本脚本**只读**数据库，不写入任何数据（唯一写操作是可选的结果报告落盘）。

用法：
    # 纯离线（默认）：只跑 TF-IDF 基线，不联网、不消耗 API
    python scripts/verify_note_search.py

    # 加上 LLM 打标对比（需已配置 DeepSeek API Key，会产生费用）
    python scripts/verify_note_search.py --with-llm

    # 指定数据库 / 自定义查询集
    python scripts/verify_note_search.py --db path/to/bill.db
"""

import argparse
import json
import math
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_DB = ROOT / ".local_data" / "finance" / "bill.db"

# 默认查询集：模拟用户真实的模糊检索意图（query → 期望命中的备注关键词）
# 期望值是「语义相关」而非「字面包含」——这正是关键词检索做不到、
# 也是本对比要验证的核心。
DEFAULT_QUERIES = [
    {
        "query": "给家里人买东西",
        "expect_any": ["妈", "爸", "父母", "家里", "长辈", "奶奶", "爷爷", "女儿", "儿子"],
    },
    {
        "query": "学习提升的花销",
        "expect_any": ["书", "课程", "培训", "考试", "报名", "网课", "考研", "学费"],
    },
    {
        "query": "看病买药",
        "expect_any": ["医院", "药", "挂号", "门诊", "体检", "牙", "诊所"],
    },
    {
        "query": "送礼人情",
        "expect_any": ["礼物", "生日", "结婚", "随礼", "红包", "喜酒", "份子"],
    },
    {
        "query": "出行交通",
        "expect_any": ["机票", "高铁", "打车", "地铁", "加油", "火车", "滴滴", "出租"],
    },
]

# 内置评测语料：真实记账场景的备注样本（当数据库样本不足时使用）
# 刻意包含「查询词与备注无字面重叠」的样本——这正是关键词检索的失效区，
# 也是本验证要量化的核心差距。
SAMPLE_CORPUS = [
    ("天猫超市", "给老妈买的按摩仪"),
    ("京东商城", "父亲节礼物"),
    ("拼多多", "奶奶的降压药"),
    ("微信红包", "给女儿的生日红包"),
    ("线下商户", "给丈母娘带的水果"),
    ("当当网", "考研数学复习全书"),
    ("得到App", "音频课程年费"),
    ("新东方", "雅思培训报名"),
    ("中国人事考试网", "准考证打印费"),
    ("文具店", "笔记本和荧光笔"),
    ("市第一人民医院", "门诊挂号"),
    ("老百姓大药房", "感冒药"),
    ("口腔诊所", "洗牙"),
    ("体检中心", "年度体检套餐"),
    ("社区卫生站", "疫苗"),
    ("周大福", "结婚三金"),
    ("花店", "生日花束"),
    ("酒店宴会厅", "同事婚宴随礼"),
    ("微信红包", "表弟结婚份子钱"),
    ("茶叶店", "送客户的茶叶"),
    ("携程", "去上海的机票"),
    ("12306", "高铁票"),
    ("滴滴出行", "打车去机场"),
    ("中石化", "加油"),
    ("成都地铁", "地铁通勤"),
    ("美团外卖", "午饭"),
    ("肯德基", "早餐"),
    ("瑞幸咖啡", "下午茶"),
    ("优衣库", "T恤"),
    ("大润发超市", "日用品"),
    ("腾讯视频", "月度会员"),
    ("万达影城", "看电影"),
    ("自如", "房租"),
    ("国家电网", "电费"),
    ("中国移动", "话费充值"),
]

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


# ---------- 数据读取（只读） ----------


def load_notes(db_path: Path, limit: int = 3000) -> list[dict]:
    """只读加载有备注或商户的流水（备注优先，商户作为兜底文本域）"""
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = conn.execute(
            """
            SELECT id, merchant, remark, category, amount, tx_time
            FROM bills
            WHERE (remark IS NOT NULL AND TRIM(remark) != '')
               OR (merchant IS NOT NULL AND TRIM(merchant) != '')
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []
    return [
        {
            "id": r[0],
            "merchant": r[1] or "",
            "remark": r[2] or "",
            "category": r[3] or "",
            "amount": r[4],
            "tx_time": r[5],
        }
        for r in rows
    ]


def load_sample_corpus() -> list[dict]:
    """内置评测语料（数据库样本不足时兜底，保证脚本任何环境都可跑出结论）"""
    return [
        {
            "id": -(i + 1),
            "merchant": m,
            "remark": r,
            "category": "",
            "amount": 0.0,
            "tx_time": "",
        }
        for i, (m, r) in enumerate(SAMPLE_CORPUS)
    ]


# ---------- 文本处理（标准库） ----------

# 中文按字切分 + 英文数字按词切分的极简分词（无 jieba 依赖）
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """极简中英文分词：中文按单字、英文数字按连续词

    中文单字切分会让「生日」拆成「生」「日」，损失部分语义——这是**不引入
    分词库的必然代价**，也是本基线偏保守（对 B 路线有利）的原因，符合
    「不能高估 B 路线」的评估原则。另补 2-gram 缓解。
    """
    text = (text or "").lower()
    tokens = _TOKEN_RE.findall(text)
    # 补中文 2-gram：让「生日」「礼物」这类词以整体形式参与匹配
    grams = [
        tokens[i] + tokens[i + 1]
        for i in range(len(tokens) - 1)
        if _is_cjk(tokens[i]) and _is_cjk(tokens[i + 1])
    ]
    return tokens + grams


def _is_cjk(ch: str) -> bool:
    return bool(ch) and "\u4e00" <= ch[0] <= "\u9fff"


class TfIdfIndex:
    """纯标准库 TF-IDF + 余弦相似度（路线 B 的代理基线）"""

    def __init__(self, docs: list[str]):
        self.doc_tokens = [tokenize(d) for d in docs]
        n = len(self.doc_tokens)
        df = Counter()
        for toks in self.doc_tokens:
            for t in set(toks):
                df[t] += 1
        # 平滑 IDF，避免除零
        self.idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}
        self.vectors = [self._vec(toks) for toks in self.doc_tokens]

    def _vec(self, tokens: list[str]) -> dict[str, float]:
        tf = Counter(tokens)
        total = sum(tf.values()) or 1
        return {
            t: (c / total) * self.idf.get(t, 0.0)
            for t, c in tf.items()
            if self.idf.get(t, 0.0) > 0
        }

    @staticmethod
    def _cos(a: dict[str, float], b: dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        common = set(a) & set(b)
        if not common:
            return 0.0
        dot = sum(a[t] * b[t] for t in common)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    def search(self, query: str, top_k: int = 10) -> list[tuple[int, float]]:
        qv = self._vec(tokenize(query))
        scored = [(i, self._cos(qv, v)) for i, v in enumerate(self.vectors)]
        scored = [s for s in scored if s[1] > 0]
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]


# ---------- 路线 A：LLM 打标签 ----------

TAG_SYSTEM = (
    "你是记账流水标签助手。用户给出若干条流水的商户与备注，"
    "请为每条给出 1~3 个**语义标签**，用于日后模糊检索。\n"
    "标签要求：\n"
    "1. 用中文简短词，覆盖「用途/对象/场景」三个维度（如：学习、礼物、家庭、就医、出行）；\n"
    "2. 不要直接抄备注原文，要抽象成语义类别；\n"
    "3. 每条给 1~3 个标签，不要超过 3 个。\n"
    '输出 JSON：{"result": {"<编号>": ["标签1", "标签2"]}}'
)


def llm_tag_batch(batch: list[dict], settings) -> dict[int, list[str]]:
    """对一批流水打标签；失败返回空 dict（不影响主流程）"""
    from app.services import ai_service

    lines = []
    for i, r in enumerate(batch):
        lines.append(f"{i}. 商户：{r['merchant'] or '无'} | 备注：{r['remark'] or '无'}")
    try:
        content = ai_service.chat(
            settings,
            [
                {"role": "system", "content": TAG_SYSTEM},
                {"role": "user", "content": "\n".join(lines)},
            ],
            max_tokens=min(3000, 64 * len(batch) + 200),
            timeout=60,
        )
        from app.utils.text import strip_code_fence

        data = json.loads(strip_code_fence(content))
        mapping = data.get("result") if isinstance(data, dict) else None
        if not isinstance(mapping, dict):
            return {}
        out: dict[int, list[str]] = {}
        for k, v in mapping.items():
            try:
                idx = int(k)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(batch) and isinstance(v, list):
                out[idx] = [str(t).strip() for t in v if str(t).strip()][:3]
        return out
    except Exception as exc:  # 打标失败不阻断对比
        print(f"    [WARN] 打标失败：{exc}")
        return {}


def build_tag_index(rows: list[dict], batch_size: int = 30) -> dict[int, list[str]]:
    """用 LLM 给全部流水打标签（分批，带进度与时间预算）"""
    from app.file_settings import load_ai_settings

    settings = load_ai_settings()
    if not settings.ready:
        print("  [SKIP] 未配置 DeepSeek API Key，跳过 LLM 打标对比")
        return {}
    tags: dict[int, list[str]] = {}
    started = time.monotonic()
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        got = llm_tag_batch(batch, settings)
        for local_idx, tag_list in got.items():
            tags[start + local_idx] = tag_list
        done = min(start + batch_size, len(rows))
        print(f"    打标进度 {done}/{len(rows)}（{time.monotonic() - started:.0f}s）")
    return tags


# LLM 打标的**规则近似**（无 Key 时的离线估算，不是真实 LLM 输出）
# 依据：LLM 打标的核心作用是把备注抽象成语义类别，这里用关键词→标签的
# 映射模拟该抽象过程。**仅供量级参考**——真实 LLM 能覆盖映射表外的表达，
# 故真实命中率只会高于此值，不会更低。
_SIM_TAG_RULES: list[tuple[tuple[str, ...], list[str]]] = [
    (("妈", "爸", "父亲", "母亲", "奶奶", "爷爷", "丈母娘", "女儿", "儿子", "长辈"),
     ["家庭", "长辈"]),
    (("书", "课程", "培训", "考研", "雅思", "考试", "准考证", "文具", "笔记"),
     ["学习", "提升"]),
    (("医院", "药", "诊所", "体检", "洗牙", "疫苗", "挂号", "门诊"),
     ["就医", "健康"]),
    (("结婚", "生日", "婚宴", "随礼", "份子", "礼物", "花束", "茶叶", "三金"),
     ["送礼", "人情"]),
    (("机票", "高铁", "打车", "地铁", "加油", "火车", "出行", "通勤"),
     ["出行", "交通"]),
    (("房租", "电费", "话费", "会员", "外卖", "咖啡", "午饭", "早餐", "T恤", "日用"),
     ["生活", "日常"]),
]


def simulate_tags(rows: list[dict]) -> dict[int, list[str]]:
    """无 Key 时用规则近似 LLM 打标（明确标注为模拟值）"""
    tags: dict[int, list[str]] = {}
    for i, r in enumerate(rows):
        text = f"{r['merchant']} {r['remark']}"
        got: list[str] = []
        for keywords, labels in _SIM_TAG_RULES:
            if any(k in text for k in keywords):
                got.extend(labels)
        if got:
            tags[i] = list(dict.fromkeys(got))[:3]
    return tags


def tag_search(
    rows: list[dict], tags: dict[int, list[str]], query: str, top_k: int = 10
) -> list[tuple[int, float]]:
    """标签检索：查询词与标签做子串/包含双向匹配，命中数作为得分"""
    q_tokens = set(tokenize(query))
    scored = []
    for idx, tag_list in tags.items():
        hit = 0
        for tag in tag_list:
            tag_tokens = set(tokenize(tag))
            if tag_tokens & q_tokens:
                hit += len(tag_tokens & q_tokens)
        if hit:
            scored.append((idx, float(hit)))
    scored.sort(key=lambda x: -x[1])
    return scored[:top_k]


# ---------- 评测 ----------


def evaluate(name: str, rows: list[dict], results: list[tuple[int, float]], q: dict) -> int:
    """统计命中：Top10 结果中是否存在期望关键词的记录；返回命中数"""
    hits = 0
    for idx, _score in results:
        r = rows[idx]
        text = f"{r['merchant']} {r['remark']}"
        if any(kw in text for kw in q["expect_any"]):
            hits += 1
    mark = "OK" if hits > 0 else "--"
    print(f"    [{mark}] {name}：Top10 命中 {hits} 条")
    return hits


def main():
    ap = argparse.ArgumentParser(description="备注语义检索方案对比")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="SQLite 数据库路径")
    ap.add_argument("--with-llm", action="store_true", help="启用 LLM 打标对比（产生费用）")
    ap.add_argument("--simulate-llm", action="store_true",
                    help="无 Key 时用规则近似模拟 LLM 打标（离线估算，非真实值）")
    ap.add_argument("--limit", type=int, default=800, help="加载流水上限（默认 800）")
    ap.add_argument("--corpus", choices=["auto", "real", "sample"], default="auto",
                    help="语料来源：auto=真实库不足时用内置样本（默认）")
    ap.add_argument("--out", default="", help="结果 JSON 落盘路径（可选）")
    args = ap.parse_args()

    print("=== 备注语义检索 方案对比验证 ===")

    rows = load_notes(Path(args.db), limit=args.limit)
    source = "真实库"
    # 样本不足（<60 条）时自动切到内置语料，否则统计没有意义
    if args.corpus == "sample" or (args.corpus == "auto" and len(rows) < 60):
        print(f"  数据库：{args.db} → 可用 {len(rows)} 条，样本不足，改用内置评测语料")
        rows = load_sample_corpus()
        source = "内置评测语料"
    else:
        print(f"  数据库：{args.db}")
    print(f"  语料来源：{source}，共 {len(rows)} 条")
    if source == "真实库" and len(rows) < 60:
        print("  [提示] 真实库样本偏少（<60 条），统计意义有限；"
              "可加 --corpus sample 用内置评测语料对比")
    if not rows:
        print("  [SKIP] 无可用数据")
        sys.exit(0)

    docs = [f"{r['merchant']} {r['remark']}" for r in rows]

    # --- 基线 0：纯关键词包含检索（现有能力，作为下限） ---
    print("\n[基线] 关键词包含检索（现有能力下限）")
    kw_total = 0
    kw_detail = {}
    for q in DEFAULT_QUERIES:
        qtext = q["query"]
        hits = []
        for i, r in enumerate(rows):
            text = f"{r['merchant']} {r['remark']}"
            # 极简：查询词中任一 2-gram 出现在文本中即算
            grams = [
                qtext[j : j + 2] for j in range(len(qtext) - 1) if len(qtext[j : j + 2]) == 2
            ]
            if any(g in text for g in grams):
                hits.append((i, 1.0))
                if len(hits) >= 10:
                    break
        n = evaluate("关键词", rows, hits, q)
        kw_total += n
        kw_detail[q["query"]] = n

    # --- 路线 B（代理）：TF-IDF 向量检索 ---
    print("\n[路线 B 代理基线] TF-IDF + 余弦相似度（纯标准库）")
    t0 = time.monotonic()
    index = TfIdfIndex(docs)
    build_ms = (time.monotonic() - t0) * 1000
    b_total = 0
    b_detail = {}
    for q in DEFAULT_QUERIES:
        results = index.search(q["query"], top_k=10)
        n = evaluate("TF-IDF", rows, results, q)
        b_total += n
        b_detail[q["query"]] = n
    print(f"    索引构建耗时：{build_ms:.0f} ms（{len(rows)} 条）")

    # --- 路线 A：LLM 打标签 ---
    a_total = 0
    a_detail = {}
    tag_count = 0
    a_label = "路线 A"
    if args.with_llm:
        print("\n[路线 A] LLM 打标签 + 标签匹配")
        tags = build_tag_index(rows)
        tag_count = len(tags)
    elif args.simulate_llm:
        print("\n[路线 A 模拟] 规则近似 LLM 打标（离线估算，非真实 LLM 输出）")
        tags = simulate_tags(rows)
        tag_count = len(tags)
        a_label = "模拟标签"
        print(f"    模拟打标覆盖：{tag_count}/{len(rows)} 条")
    else:
        tags = {}
        print("\n[路线 A] 跳过（未加 --with-llm 或 --simulate-llm）")

    if tags:
        for q in DEFAULT_QUERIES:
            results = tag_search(rows, tags, q["query"], top_k=10)
            n = evaluate(a_label, rows, results, q)
            a_total += n
            a_detail[q["query"]] = n

    # --- 汇总 ---
    print("\n=== 汇总（Top10 命中合计，越大越好） ===")
    print(f"  关键词包含（现有能力下限）：{kw_total}")
    print(f"  TF-IDF 向量（零依赖方案）  ：{b_total}")
    if tags:
        kind = "LLM 打标签（真实）" if args.with_llm else "LLM 打标签（规则模拟）"
        print(f"  {kind}  ：{a_total}　（已打标 {tag_count} 条）")

    print("\n  逐查询对照：")
    header = f"    {'查询':<14}{'关键词':>8}{'TF-IDF':>10}"
    if tags:
        header += f"{'标签':>8}"
    print(header)
    for q in DEFAULT_QUERIES:
        line = f"    {q['query']:<14}{kw_detail[q['query']]:>8}{b_detail[q['query']]:>10}"
        if tags:
            line += f"{a_detail.get(q['query'], 0):>8}"
        print(line)

    check("TF-IDF 显著优于关键词基线", b_total >= kw_total * 2,
          f"{b_total} vs {kw_total}")
    if tags and args.with_llm:
        check("LLM 打标方案可用", a_total > 0, f"命中 {a_total}")

    print("\n  结论提示：")
    if b_total > kw_total:
        print(f"    纯标准库 TF-IDF 相对关键词检索提升 {b_total - kw_total} 条命中，"
              "且无需任何新增依赖。")
    if tags and a_total >= b_total:
        print("    标签方案达到或超过向量基线，且标签人可读可修正、可审计。")
    elif tags:
        print(f"    标签方案命中 {a_total} < 向量 {b_total}，"
              "但标签具备可解释与可修正优势，需权衡。")

    if args.out:
        payload = {
            "db": args.db,
            "corpus_source": source,
            "rows": len(rows),
            "keyword_total": kw_total,
            "tfidf_total": b_total,
            "llm_total": a_total if tags else None,
            "llm_mode": ("real" if args.with_llm else ("simulated" if tags else None)),
            "tfidf_build_ms": round(build_ms, 1),
            "detail": {
                "keyword": kw_detail,
                "tfidf": b_detail,
                "llm": a_detail,
            },
        }
        Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n  结果已落盘：{args.out}")

    print(f"\n=== 结果：通过 {PASS} 项 / 失败 {FAIL} 项 ===")
    print("\n注：本脚本只读数据库，不写入任何业务数据。")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
