"""账单文件解析器（微信 xlsx / 支付宝·京东·云闪付 csv）

平台解析器统一在本模块注册（单一注册表），供上传接口与来源识别共用，
新增平台时只需实现 BaseParser 并在 PARSERS 中登记。
"""

from dataclasses import dataclass
from typing import Optional

from app.parsers.alipay_parser import AlipayParser
from app.parsers.base import BaseParser
from app.parsers.jd_parser import JdParser
from app.parsers.unionpay_parser import UnionPayParser
from app.parsers.wechat_parser import WechatParser


@dataclass(frozen=True)
class ParserSpec:
    """单个平台的解析器描述"""

    source: str  # 来源标识（wechat/alipay/jd/unionpay）
    cls: type  # 解析器类
    ext: str  # 该平台账单的文件后缀（含点，如 ".xlsx"）


# 平台注册表：上传接口按 source 取解析器与允许后缀，detect 按来源实例化
PARSERS: dict[str, ParserSpec] = {
    spec.source: spec
    for spec in (
        ParserSpec("wechat", WechatParser, ".xlsx"),
        ParserSpec("alipay", AlipayParser, ".csv"),
        ParserSpec("jd", JdParser, ".csv"),
        ParserSpec("unionpay", UnionPayParser, ".csv"),
    )
}


def get_parser_spec(source: str) -> Optional[ParserSpec]:
    """来源标识 → 解析器描述；未知来源返回 None"""
    return PARSERS.get(source)


def build_parser(source: str) -> Optional[BaseParser]:
    """来源标识 → 解析器实例；未知来源返回 None"""
    spec = PARSERS.get(source)
    return spec.cls() if spec else None
