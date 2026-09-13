/* 资产快照接口（/api/asset） */
import { api, toQuery } from "./client";

export const assetTrend = () => api("/api/asset/trend");
export const listAssets = (params = {}) => api(`/api/asset${toQuery(params)}`);
export const createAsset = (payload) =>
  api("/api/asset", { method: "POST", body: JSON.stringify(payload) });
export const updateAsset = (id, payload) =>
  api(`/api/asset/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(payload) });
export const deleteAsset = (id) =>
  api(`/api/asset/${encodeURIComponent(id)}`, { method: "DELETE" });
