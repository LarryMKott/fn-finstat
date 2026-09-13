/* 智能分类（DeepSeek）接口（/api/ai） */
import { api } from "./client";

export const aiConfig = () => api("/api/ai/config");
export const saveAIConfig = (payload) =>
  api("/api/ai/config", { method: "PUT", body: JSON.stringify(payload) });
export const testAI = (payload) =>
  api("/api/ai/test", { method: "POST", body: JSON.stringify(payload), timeout: 90_000 });
export const classifyBills = (scope) =>
  api("/api/ai/classify", { method: "POST", body: JSON.stringify({ scope }), timeout: 300_000 });
export const aiMonthReport = (month) =>
  api("/api/ai/report", { method: "POST", body: JSON.stringify({ month }), timeout: 90_000 });
