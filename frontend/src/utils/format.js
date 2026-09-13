/* 展示格式化与表单工具（金额/类型/标签/时间拆分） */

export const fmtMoney = (v) =>
  "¥" + Number(v || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/* 带符号金额：用于流水表，一眼区分进出方向。
 * 遵循中国用户直觉：收入为正、支出为负，配合语义色（收入红 / 支出绿）扫读 */
export function fmtSignedMoney(v, txType) {
  const n = Number(v || 0);
  const sign = txType === "income" ? "+" : txType === "expense" ? "-" : "";
  return sign + fmtMoney(n);
}

/* 金额语义色 class：转账保持中性，避免误导为收支 */
export function amountClass(txType) {
  if (txType === "income") return "amount--income";
  if (txType === "expense") return "amount--expense";
  return "amount--neutral";
}

export const fmtType = (t) => ({ expense: "支出", income: "收入", transfer: "转账" }[t] || t);

const ACCOUNT_LABEL = { wechat: "微信", alipay: "支付宝", jd: "京东", unionpay: "云闪付" };
export const fmtAccount = (a) => ACCOUNT_LABEL[a] || a;

/* 账户色点：用品牌色做视觉锚点，便于在密集列表中快速定位账户来源 */
export function accountDotClass(a) {
  return ["wechat", "alipay", "jd", "unionpay"].includes(a) ? `acct__dot--${a}` : "";
}

const TAG_CLASS = { expense: "tag-expense", income: "tag-income", transfer: "tag-transfer" };
export const tagClass = (t) => TAG_CLASS[t] || "tag-transfer";

/* 标签：库内为逗号分隔字符串，界面按数组展示/编辑 */
export const splitTags = (tags) =>
  (tags || "").split(",").map((t) => t.trim()).filter(Boolean);

/* 日期时间拆分展示：日期为主行、时间为次要信息，提升流水列表的扫读速度。
 * 库内格式为 "YYYY-MM-DD HH:MM:SS" */
export function splitDateTime(t) {
  const s = String(t || "").trim();
  if (!s) return { date: "-", time: "" };
  const [d, time = ""] = s.split(" ");
  const hm = time.slice(0, 5);
  /* 同一天时省略年份前缀，减少视觉噪音 */
  const nowYear = String(new Date().getFullYear());
  const date = d.startsWith(nowYear + "-") ? d.slice(5) : d;
  return { date, time: hm };
}

export const emptyForm = () => ({
  tx_time: "",
  account: "wechat",
  tx_type: "expense",
  merchant: "",
  amount: "",
  category: "其他",
  tx_id: "",
  remark: "",
  tags: "",
  reimbursed: false,
});

/* 入库时间统一为秒级，避免分钟/秒级混排影响文本排序 */
export function normalizeTxTime(v) {
  let t = (v || "").replace("T", " ");
  if (t.length === 16) t += ":00";
  return t;
}

/** 字节大小 → 可读文本（备份/账单文件尺寸展示） */
export function fmtSize(bytes) {
  const n = Number(bytes || 0);
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / 1024 / 1024).toFixed(1) + " MB";
}
