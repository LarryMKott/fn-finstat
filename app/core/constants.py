"""跨层共享的领域词汇常量：收支类型 / 账户类型 / 资产类型 / 家庭角色

放在 core（最底层，全部分层可引用）：解析器产出收支类型、服务层校验入参枚举、
导出与 AI 报告取中文标签、备份恢复做合法值白名单——各层引用同一份定义，
新增取值（如新平台账户）只改这里，不再各处散落字面量。
"""

# ---- 收支类型（bill.tx_type 列的合法取值）----
TX_TYPES = ("expense", "income", "transfer")
TX_TYPE_EXPENSE = "expense"
TX_TYPE_INCOME = "income"
TX_TYPE_TRANSFER = "transfer"

# 收支类型 → 中文标签（导出表头、AI 报告文案共用）
TX_TYPE_LABELS = {
    TX_TYPE_EXPENSE: "支出",
    TX_TYPE_INCOME: "收入",
    TX_TYPE_TRANSFER: "转账",
}

# ---- 账户类型（bill.account 列的合法取值 = 平台注册表 PARSERS 的来源标识）----
ACCOUNTS = ("wechat", "alipay", "jd", "unionpay")

# 账户类型 → 中文标签（导出表头用）
ACCOUNT_LABELS = {
    "wechat": "微信",
    "alipay": "支付宝",
    "jd": "京东",
    "unionpay": "云闪付",
}

# ---- 资产快照类型（asset_snapshots.asset_type 的合法取值）----
ASSET_TYPES = ("asset", "liability")
ASSET_TYPE_ASSET = "asset"  # 资产
ASSET_TYPE_LIABILITY = "liability"  # 负债

# ---- 家庭角色（family_members.role 的合法取值；admin = 创建人）----
FAMILY_ROLES = ("admin", "member")
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
