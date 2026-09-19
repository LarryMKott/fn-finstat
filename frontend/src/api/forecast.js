/* 预测与建议接口（/api/forecast）：现金流预测 + 预算建议（只读，采纳走 /api/budget） */
import { api, toQuery } from "./client";

/* exclude 为数组：同一 key 需重复出现在查询串（?exclude=a&exclude=b），toQuery 的
 * set 语义会互相覆盖，这里手工拼接 */
export function cashFlow({ horizon = 90, exclude = [] } = {}) {
  const qs = new URLSearchParams();
  qs.set("horizon", String(horizon));
  for (const key of exclude) qs.append("exclude", key);
  const s = qs.toString();
  return api(`/api/forecast${s ? `?${s}` : ""}`);
}

export const budgetSuggestions = (month) =>
  api(`/api/forecast/budget-suggestions${toQuery({ month })}`);

/* 固定支出 vs 弹性支出拆分（T-1.5）：近 6 个完整月，必选项 / 可砍项 */
export const expenseStructure = (ledgerId) =>
  api(`/api/forecast/expense-structure${toQuery({ ledger_id: ledgerId })}`);
