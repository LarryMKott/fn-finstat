"""备注语义检索核心算法（AI-8）：零依赖 TF-IDF + 余弦相似度

技术路线（脑洞清单 §7.4 已拍板）：纯标准库词频统计，不引入本地向量模型
（sentence-transformers ≈1.5GB 含 torch，违背项目零依赖红线）。实测收益：
「给家里人买东西」这类与备注无字面重叠的模糊问法，Top10 命中从关键词
检索的 1 提到 9（`scripts/verify_note_search.py` 的评测数据）。

检索域约定：调用方把「商户 + 备注」拼成一个文档字符串传入。中文按单字
切分并补相邻双字（让「生日」「礼物」这类词以整体参与匹配），英文数字按
连续词切分——这是不引入分词库（jieba）的代价换来的零依赖口径。
"""

import math
import re
from collections import Counter

# 中文按字切分 + 英文数字按词切分的极简分词
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]")


def _is_cjk(ch: str) -> bool:
    return bool(ch) and "\u4e00" <= ch[0] <= "\u9fff"


def tokenize(text: str) -> list[str]:
    """中英文混合分词：中文单字 + 相邻双字（2-gram）、英文数字按连续词

    中文单字切分会损失部分语义（「生日」拆成「生」「日」），补 2-gram
    缓解；查询与文档走同一切分，保证词空间一致。
    """
    text = str(text or "").lower()
    tokens = _TOKEN_RE.findall(text)
    grams = [
        tokens[i] + tokens[i + 1]
        for i in range(len(tokens) - 1)
        if _is_cjk(tokens[i]) and _is_cjk(tokens[i + 1])
    ]
    return tokens + grams


class TfIdfIndex:
    """内存 TF-IDF 索引：build O(总词数)，search O(文档数 × 查询词数)

    量级约定：几千条流水的建索引在纯 Python 下为毫秒级（见验证脚本实测），
    因此不做持久化缓存、每次检索现场重建；更大规模时由调用方限制语料条数。
    """

    def __init__(self, docs: list[str]):
        self.doc_tokens = [tokenize(d) for d in docs]
        n = len(self.doc_tokens)
        df: Counter[str] = Counter()
        for toks in self.doc_tokens:
            for t in set(toks):
                df[t] += 1
        # 平滑 IDF：未在语料出现过的查询词 idf=0，不参与打分
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

    def search(
        self, query: str, top_k: int = 10, min_score: float = 0.0
    ) -> list[tuple[int, float]]:
        """按余弦相似度返回最相关的至多 top_k 个文档（索引下标, 得分）

        得分为 0（无词面交集）与低于 min_score 的噪声命中不返回；
        同分按索引下标稳定排序，保证同一语料下结果可复现。
        """
        qv = self._vec(tokenize(query))
        if not qv or top_k <= 0:
            return []
        scored: list[tuple[int, float]] = []
        for i, v in enumerate(self.vectors):
            score = self._cos(qv, v)
            if score > 0 and score >= min_score:
                scored.append((i, score))
        return sorted(scored, key=lambda x: (-x[1], x[0]))[:top_k]
