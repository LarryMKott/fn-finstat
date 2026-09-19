/* 报销 / 垫付工作流接口（/api/reimb，T-7.4）
 * 报销单 = 一组支出的回收进度跟踪：待提交 → 已提交 → 部分到账 → 已结清；
 * 挂单/摘单会同步流水的报销标记，既有报销筛选零破坏 */
import { api } from "./client";

export const listReimbursements = () => api("/api/reimb");
export const createReimbursement = (title, note = "") =>
  api("/api/reimb", { method: "POST", body: JSON.stringify({ title, note }) });
export const updateReimbursement = (id, payload) =>
  api(`/api/reimb/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
export const deleteReimbursement = (id) =>
  api(`/api/reimb/${encodeURIComponent(id)}`, { method: "DELETE" });
export const reimbBills = (id) =>
  api(`/api/reimb/${encodeURIComponent(id)}/bills`);
export const attachBillsToReimb = (id, ids) =>
  api(`/api/reimb/${encodeURIComponent(id)}/bills`, {
    method: "POST",
    body: JSON.stringify({ ids }),
  });
export const detachBillsFromReimb = (id, ids) =>
  api(`/api/reimb/${encodeURIComponent(id)}/bills`, {
    method: "DELETE",
    body: JSON.stringify({ ids }),
  });
