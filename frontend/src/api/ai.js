/* 智能分类（DeepSeek）接口（/api/ai） */
import { api, toQuery } from "./client";

export const aiConfig = () => api("/api/ai/config");
export const saveAIConfig = (payload) =>
  api("/api/ai/config", { method: "PUT", body: JSON.stringify(payload) });
export const testAI = (payload) =>
  api("/api/ai/test", { method: "POST", body: JSON.stringify(payload), timeout: 90_000 });
export const classifyBills = (scope) =>
  api("/api/ai/classify", { method: "POST", body: JSON.stringify({ scope }), timeout: 300_000 });
export const aiMonthReport = (month) =>
  api("/api/ai/report", { method: "POST", body: JSON.stringify({ month }), timeout: 90_000 });

/* 周期报告扩展（月/季/半年/年）+ 归档
 * - 生成预览不落库（DeepSeek 调用产生费用）
 * - 归档按 (user_id, period_type, period_value) 唯一键覆盖旧版本
 * - 查看归档不消耗配额 */
export const aiGenerateReport = (periodType, periodValue) =>
  api("/api/ai/report/generate", {
    method: "POST",
    body: JSON.stringify({ period_type: periodType, period_value: periodValue }),
    timeout: 90_000,
  });

export const aiArchiveReport = ({ periodType, periodValue, title, content, statsSummary }) =>
  api("/api/ai/report/archive", {
    method: "POST",
    body: JSON.stringify({
      period_type: periodType,
      period_value: periodValue,
      title,
      content,
      stats_summary: statsSummary ?? null,
    }),
  });

export const aiListArchived = (periodType = "") => {
  const qs = toQuery(periodType ? { period_type: periodType } : {});
  return api(`/api/ai/report/list${qs}`);
};

export const aiGetArchived = (id) => api(`/api/ai/report/${id}`);

export const aiDeleteArchived = (id) =>
  api(`/api/ai/report/${id}`, { method: "DELETE" });
