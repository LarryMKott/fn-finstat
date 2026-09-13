"""CSV 账单公共解析逻辑：编码探测 + 金额文本清理 + 列头别名匹配（各平台共用）

各平台导出的 CSV 列名随 App 版本略有差异，采用「别名列表」按列头匹配：
子类声明 REQUIRED（必须存在的列）与 ALIASES（规范列名 → 候选表头列表），
解析时扫描前若干行找到同时命中全部必需列的表头行，再逐行转标准流水。
"""

import csv
import io
from abc import abstractmethod
from pathlib import Path

from app.parsers.base import BaseParser
from app.utils.amount import normalize_amount

# 探测顺序：严格编码优先。gb18030 几乎能解码任意字节序列且不报错，
# 若排在前会把 UTF-8 文件错误解码为乱码，导致列头永远匹配不上。
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")

# 金额文本清理：去除货币符号/千分位/加号
_AMOUNT_STRIP = ("¥", "￥", ",", "+", " ")


def decode_bytes(raw: bytes) -> str:
    """字节序列多编码探测解码；全部失败时按 gb18030 替换坏字节兜底"""
    for enc in ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gb18030", errors="replace")


def decode_csv(file_path: Path) -> str:
    """CSV 文件读取 + 多编码探测解码"""
    return decode_bytes(file_path.read_bytes())


def strip_amount_text(text: str) -> str:
    """金额文本清理：去除货币符号/千分位/加号与首尾空白（不处理负号，保留原语义）"""
    cleaned = text
    for ch in _AMOUNT_STRIP:
        cleaned = cleaned.replace(ch, "")
    return cleaned.strip()


def clean_amount(text: str) -> float | None:
    """金额文本 → 正浮点；无法解析或非正数返回 None"""
    cleaned = strip_amount_text(text).rstrip("-").strip()  # 兼容 "12.34-" 后置负号
    if not cleaned:
        return None
    try:
        amount = normalize_amount(abs(float(cleaned)))
    except ValueError:
        return None
    return amount if amount > 0 else None


class TolerantCsvParser(BaseParser):
    """基于列头别名的宽容 CSV 账单解析器基类"""

    # 必须命中的规范列（缺一即认为不是表头行）
    REQUIRED: tuple[str, ...] = ("tx_time", "amount")
    # 规范列名 → 候选表头（按顺序取第一个命中的）
    ALIASES: dict[str, tuple[str, ...]] = {}
    # 交易关闭类状态（命中则跳过该行）
    SKIP_STATUS: set[str] = set()

    def parse(self, file_path: Path) -> list[dict]:
        reader = csv.reader(io.StringIO(decode_csv(file_path)))
        mapping: dict[str, int] | None = None
        records: list[dict] = []
        for row in reader:
            if not row:
                continue
            cells = [c.strip() for c in row]
            if mapping is None:
                mapping = self._match_header(cells)
                continue
            record = self._row_to_record(self._row_dict(cells, mapping))
            if record:
                records.append(record)
        return records

    def _match_header(self, cells: list[str]) -> dict[str, int] | None:
        """当前行是否为表头行：全部必需列的别名都能命中时返回 规范列→下标 映射"""
        mapping: dict[str, int] = {}
        for canonical, aliases in self.ALIASES.items():
            idx = -1
            for alias in aliases:
                try:
                    idx = cells.index(alias)
                    break
                except ValueError:
                    continue
            if idx < 0:
                if canonical in self.REQUIRED:
                    return None
                continue  # 可选列缺失：允许（取值时回落默认值）
            mapping[canonical] = idx
        return mapping

    def _row_dict(self, cells: list[str], mapping: dict[str, int]) -> dict[str, str]:
        """按列映射取值；越界/缺列返回空串"""
        return {
            key: (cells[idx] if idx < len(cells) else "")
            for key, idx in mapping.items()
        }

    @abstractmethod
    def _row_to_record(self, row: dict[str, str]) -> dict | None:
        """规范列字典 → 标准流水；无效行返回 None"""
        raise NotImplementedError
