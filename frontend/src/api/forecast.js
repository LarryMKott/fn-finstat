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

/* 订阅侦探（AI-5）：订阅时间线 / 台阶涨价 / 疑似僵尸订阅（近 12 个完整月） */
export const subscriptions = (ledgerId) =>
  api(`/api/forecast/subscriptions${toQuery({ ledger_id: ledgerId })}`);

/* What-if 反事实模拟（AI-9）：基线 = 分类月均（前 8 类）+ 月结余 + 目标进度；
 * 情景 = 调整清单线性外推，只读计算不落库 */
export const whatIfBaseline = (ledgerId) =>
  api(`/api/forecast/what-if${toQuery({ ledger_id: ledgerId })}`);

export const whatIfScenario = (adjustments, months = 12) =>
  api("/api/forecast/what-if", {
    method: "POST",
    body: JSON.stringify({
      adjustments: adjustments.map(({ category, monthly_amount }) => ({
        category,
        monthly_amount: Number(monthly_amount),
      })),
      months,
    }),
  });
