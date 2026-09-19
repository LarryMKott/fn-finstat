/* 储蓄目标接口（/api/savings-goals，T-1.4）
 * 进度 = 目标起始日以来累计净结余（收入 − 支出），由流水实时计算 */
import { api } from "./client";

export const savingsGoals = () => api("/api/savings-goals");
export const createSavingsGoal = (payload) =>
  api("/api/savings-goals", { method: "POST", body: JSON.stringify(payload) });
export const updateSavingsGoal = (id, payload) =>
  api(`/api/savings-goals/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
export const deleteSavingsGoal = (id) =>
  api(`/api/savings-goals/${encodeURIComponent(id)}`, { method: "DELETE" });
