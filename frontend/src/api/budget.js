/* 月度预算接口（/api/budget） */
import { api, toQuery } from "./client";

export const budgetOverview = (month) => api(`/api/budget${toQuery({ month })}`);
export const upsertBudget = (payload) =>
  api("/api/budget", { method: "PUT", body: JSON.stringify(payload) });
export const deleteBudget = (id) =>
  api(`/api/budget/${encodeURIComponent(id)}`, { method: "DELETE" });
