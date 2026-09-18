/* 账本管理接口（/api/ledgers，T-7.1）
 * 账本是流水/预算/资产快照的归属维度：读接口全部账号可用（筛选器用），
 * 写接口限应用管理员（403 时前端隐藏入口） */
import { api } from "./client";

export const listLedgers = () => api("/api/ledgers");
export const createLedger = (name, remark = "") =>
  api("/api/ledgers", { method: "POST", body: JSON.stringify({ name, remark }) });
export const updateLedger = (id, payload) =>
  api(`/api/ledgers/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
export const deleteLedger = (id) =>
  api(`/api/ledgers/${encodeURIComponent(id)}`, { method: "DELETE" });
