/* 开放 API Token 接口（/api/tokens，T-1.2）：只读凭证，明文仅签发时返回一次 */
import { api } from "./client";

export const listTokens = () => api("/api/tokens");
export const createToken = (name) =>
  api("/api/tokens", { method: "POST", body: JSON.stringify({ name }) });
export const revokeToken = (id) =>
  api(`/api/tokens/${encodeURIComponent(id)}`, { method: "DELETE" });
