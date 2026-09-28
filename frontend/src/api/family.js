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

/* 家庭预算（T-7.3 共享预算）：金额家庭管理员设定，进度按全体成员支出汇总 */
export const familyBudgetOverview = (month) =>
  api(`/api/family/budgets${toQuery({ month })}`);
export const upsertFamilyBudget = (payload) =>
  api("/api/family/budgets", { method: "PUT", body: JSON.stringify(payload) });
export const deleteFamilyBudget = (id) =>
  api(`/api/family/budgets/${encodeURIComponent(id)}`, { method: "DELETE" });

/* 家庭月度 AI 复盘（AI-10）：只基于聚合值，隐私门控前移到上下文组装 */
export const familyAIReview = (month) =>
  api("/api/family/ai-review", { method: "POST", body: JSON.stringify({ month }) });
