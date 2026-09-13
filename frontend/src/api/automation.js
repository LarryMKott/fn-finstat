/* 自动化任务接口（/api/settings/automation） */
import { api, toQuery } from "./client";

/** 任务列表 */
export const listAutomationTasks = () => api("/api/settings/automation");

/** 手动立即执行一次 */
export const runAutomationTask = (taskKey) =>
  api(`/api/settings/automation/${encodeURIComponent(taskKey)}/run`, { method: "POST" });

/** 启用/停用任务 */
export const toggleAutomationTask = (taskKey, enabled) =>
  api(`/api/settings/automation/${encodeURIComponent(taskKey)}/toggle`, {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });

/** 修改执行间隔（分钟，1..10080） */
export const updateAutomationInterval = (taskKey, intervalMinutes) =>
  api(`/api/settings/automation/${encodeURIComponent(taskKey)}`, {
    method: "PUT",
    body: JSON.stringify({ interval_minutes: intervalMinutes }),
  });

/** 运行历史（倒序，默认最近 20 条） */
export const listAutomationRuns = (taskKey, limit = 20) =>
  api(`/api/settings/automation/${encodeURIComponent(taskKey)}/runs${toQuery({ limit })}`);
