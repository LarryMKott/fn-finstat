"""文本工具：LLM 输出清洗（智能分类 / AI 报告 / 自然语言查询共用）"""

from __future__ import annotations


def strip_code_fence(text: str) -> str:
    """剥掉 LLM 输出首尾的 ``` / ```json 围栏，返回去空白后的内容

    模型偶发把 JSON 包进 Markdown 围栏（```json ... ```），三处解析逻辑
    （ai_service、nl_query）共用此处剥壳；无围栏时原样去空白返回。
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    parts = stripped.split("```", 2)
    inner = parts[1] if len(parts) > 1 else stripped
    if inner.startswith("json"):
        inner = inner[4:]
    return inner.strip()
