/* 消费分类接口（/api/category） */
import { api } from "./client";

export const listCategories = () => api("/api/category");
export const getCategory = (id) => api(`/api/category/${encodeURIComponent(id)}`);
export const createCategory = (name) =>
  api("/api/category", { method: "POST", body: JSON.stringify({ name }) });
export const renameCategory = (id, name) =>
  api(`/api/category/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify({ name }) });
export const deleteCategory = (id) =>
  api(`/api/category/${encodeURIComponent(id)}`, { method: "DELETE" });
