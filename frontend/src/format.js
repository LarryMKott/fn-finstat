/* 展示格式化与表单工具 */

export const fmtMoney = (v) =>
  "¥" + Number(v || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const fmtType = (t) => ({ expense: "支出", income: "收入", transfer: "转账" }[t] || t);

export const fmtAccount = (a) => (a === "alipay" ? "支付宝" : "微信");

const TAG_CLASS = { expense: "tag-expense", income: "tag-income", transfer: "tag-transfer" };
export const tagClass = (t) => TAG_CLASS[t] || "tag-transfer";

/* 本地时间（datetime-local 输入框格式），替代 toISOString 的 UTC 偏移问题 */
export function nowLocalMinute() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
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
});

/* 入库时间统一为秒级，避免分钟/秒级混排影响文本排序 */
export function normalizeTxTime(v) {
  let t = (v || "").replace("T", " ");
  if (t.length === 16) t += ":00";
  return t;
}
