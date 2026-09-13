/* 应用设置接口（/api/settings；备份/恢复仅管理员） */
import { api, apiUrl, toQuery } from "./client";

export const aboutInfo = () => api("/api/settings/about");
export const databaseInfo = () => api("/api/settings/database");
export const claimLegacyBills = () =>
  api("/api/settings/user/claim", { method: "POST" });
export const testTargetDatabase = (payload) =>
  api("/api/settings/database/test", { method: "POST", body: JSON.stringify(payload), timeout: 60_000 });
export const migrateDatabase = (payload) =>
  api("/api/settings/database/migrate", { method: "POST", body: JSON.stringify(payload), timeout: 300_000 });
export const runtimeLogs = (lines = 300) =>
  api(`/api/settings/logs${toQuery({ lines })}`);

/** 完整日志下载链接（浏览器直接下载） */
export const logsDownloadUrl = () => apiUrl("/api/settings/logs/download");
/** 全量备份下载链接（浏览器直接下载，仅管理员） */
export const backupDownloadUrl = () => apiUrl("/api/settings/backup");

/** 从备份 JSON 恢复（formData 含 file 字段；replace=true 为覆盖模式） */
export const restoreBackup = (formData) =>
  api("/api/settings/restore", { method: "POST", body: formData, timeout: 120_000 });
