"""账单解析器抽象基类"""

from abc import ABC, abstractmethod
from pathlib import Path

from app.core.constants import TX_TYPE_EXPENSE, TX_TYPE_INCOME


class BaseParser(ABC):
    """账单解析器基类。新增平台解析器时继承并实现 parse()。

    每条解析结果字段：
        tx_time   交易时间字符串
        account   账户类型（wechat/alipay/...）
        tx_type   收支类型（expense/income/transfer）
        merchant  交易对方/商户名称
        amount    金额（正数）
        category  消费分类（留空则由服务层按关键词自动归类）
        tx_id     交易唯一流水号
        remark    备注
    """

    account: str = "unknown"

    @abstractmethod
    def parse(self, file_path: Path) -> list[dict]:
        """解析账单文件，返回标准化流水字典列表"""
        raise NotImplementedError


def direction_to_type(direction: str) -> str | None:
    """「收/支」列文本 → 标准收支类型；无法识别返回 None（各平台自行决定兜底）"""
    if direction == "收入":
        return TX_TYPE_INCOME
    if direction == "支出":
        return TX_TYPE_EXPENSE
    return None
