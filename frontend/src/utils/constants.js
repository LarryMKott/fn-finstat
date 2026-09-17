/* 业务常量：账户 / 收支类型 / 平台账单信息（多组件共用，单一来源） */

/** 账户类型（key 与后端 account 字段一致） */
export const ACCOUNTS = [
  { value: "wechat", label: "微信" },
  { value: "alipay", label: "支付宝" },
  { value: "jd", label: "京东" },
  { value: "unionpay", label: "云闪付" },
];

/** 收支类型 */
export const TX_TYPES = [
  { value: "expense", label: "支出" },
  { value: "income", label: "收入" },
  { value: "transfer", label: "转账" },
];

/** 账单平台信息：上传卡片与 NAS 来源徽标共用 */
export const PLATFORMS = [
  {
    key: "wechat",
    title: "微信支付账单",
    abbr: "微",
    ext: "xlsx",
    hint: "微信 → 我 → 服务 → 钱包 → 账单 → 右上角「账单下载」→ 导出 Excel",
    accept: ".xlsx",
  },
  {
    key: "alipay",
    title: "支付宝账单",
    abbr: "支",
    ext: "csv",
    hint: "支付宝 → 我的 → 账单 → 交易流水证明 → 导出 CSV 流水",
    accept: ".csv",
  },
  {
    key: "jd",
    title: "京东金融账单",
    abbr: "京",
    ext: "csv",
    hint: "京东金融 App → 我的 → 账单 → 收支统计 → 导出 CSV 流水",
    accept: ".csv",
  },
  {
    key: "unionpay",
    title: "云闪付账单",
    abbr: "云",
    ext: "csv",
    hint: "云闪付 App → 首页「账单」→ 导出交易明细 CSV",
    accept: ".csv",
  },
];

/** NAS 来源徽标（key 与后端识别结果一致） */
const SOURCE_META = {
  wechat: { abbr: "微", label: "微信支付" },
  alipay: { abbr: "支", label: "支付宝" },
  jd: { abbr: "京", label: "京东金融" },
  unionpay: { abbr: "云", label: "云闪付" },
  unknown: { abbr: "?", label: "未识别" },
};

export const sourceMeta = (source) => SOURCE_META[source] || SOURCE_META.unknown;

/** 通知事件类型展示名（与后端 notify_service.EVENT_TYPES 对应；
 * 列表行的类型徽标用，配置页开关文案以后端下发的 label 为准） */
export const NOTIFY_EVENT_LABELS = {
  budget_exceeded: "预算超支",
  budget_near_limit: "预算接近上限",
  report_ready: "报告就绪",
  import_done: "自动导入完成",
  task_failed: "定时任务异常",
};
