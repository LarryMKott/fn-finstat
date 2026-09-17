/* 账单流水接口（/api/bill） */
import { api, apiUrl, toQuery } from "./client";

export const listBills = (params = {}) => api(`/api/bill/list${toQuery(params)}`);

/** 导出链接：浏览器直接下载，不走 api() 解包 */
export const exportBillsUrl = (params = {}) =>
  apiUrl(`/api/bill/export${toQuery(params)}`);

export const createBill = (payload) =>
  api("/api/bill", { method: "POST", body: JSON.stringify(payload) });
export const updateBill = (id, payload) =>
  api(`/api/bill/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(payload) });
export const deleteBill = (id) =>
  api(`/api/bill/${encodeURIComponent(id)}`, { method: "DELETE" });

export const batchBills = (payload) =>
  api("/api/bill/batch", { method: "POST", body: JSON.stringify(payload) });

export const listRecycle = (page = 1, pageSize = 20) =>
  api(`/api/bill/recycle${toQuery({ page, page_size: pageSize })}`);
export const restoreBills = (ids) =>
  api("/api/bill/recycle/restore", { method: "POST", body: JSON.stringify({ ids }) });
export const purgeBills = (ids) =>
  api("/api/bill/recycle", { method: "DELETE", body: JSON.stringify({ ids }) });
export const emptyRecycle = () => api("/api/bill/recycle/empty", { method: "POST" });
