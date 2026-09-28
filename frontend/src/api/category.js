/* 消费分类接口（/api/category） */
import { api } from "./client";

export const listCategories = () => api("/api/category");
export const getCategoryTree = () => api("/api/category/tree");
export const getCategory = (id) => api(`/api/category/${encodeURIComponent(id)}`);
export const createCategory = (name, parentId = null) =>
  api("/api/category", {
    method: "POST",
    body: JSON.stringify({ name, parent_id: parentId }),
  });
export const renameCategory = (id, name) =>
  api(`/api/category/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify({ name }) });
export const deleteCategory = (id) =>
  api(`/api/category/${encodeURIComponent(id)}`, { method: "DELETE" });

/* 分类关键词（v1.1）：列表公开，写操作后端要求管理员 */
export const listKeywords = (categoryId) =>
  api(`/api/category/${encodeURIComponent(categoryId)}/keywords`);
export const addKeywords = (categoryId, keywords) =>
  api(`/api/category/${encodeURIComponent(categoryId)}/keywords`, {
    method: "POST",
    body: JSON.stringify({ keywords }),
  });
export const toggleKeyword = (keywordId, enabled) =>
  api(`/api/category/keyword/${encodeURIComponent(keywordId)}`, {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
export const deleteKeyword = (keywordId) =>
  api(`/api/category/keyword/${encodeURIComponent(keywordId)}`, { method: "DELETE" });
