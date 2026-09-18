/* 家庭空间接口（/api/family，T-7.2）
 * 家庭 = 多个飞牛账号的聚合容器：成员各自记账，家庭页只给聚合值；
 * 成员明细默认互不可见，allow_detail_view 开启后可只读互看 */
import { api, toQuery } from "./client";

export const getFamily = () => api("/api/family");
export const createFamily = (name) =>
  api("/api/family", { method: "POST", body: JSON.stringify({ name }) });
export const joinFamily = (code) =>
  api("/api/family/join", { method: "POST", body: JSON.stringify({ code }) });
export const leaveFamily = () => api("/api/family/leave", { method: "POST" });
export const disbandFamily = () => api("/api/family", { method: "DELETE" });
export const updateFamilySettings = (allowDetailView) =>
  api("/api/family/settings", {
    method: "PUT",
    body: JSON.stringify({ allow_detail_view: allowDetailView }),
  });
export const regenerateInviteCode = () =>
  api("/api/family/invite/regenerate", { method: "POST" });
export const removeFamilyMember = (userId) =>
  api(`/api/family/members/${encodeURIComponent(userId)}`, { method: "DELETE" });
export const familySummary = (month) =>
  api(`/api/family/summary${toQuery({ month })}`);
/* 成员流水（只读，需家庭开启明细可见） */
export const familyMemberBills = (userId, params = {}) =>
  api(`/api/family/members/${encodeURIComponent(userId)}/bills${toQuery(params)}`);
