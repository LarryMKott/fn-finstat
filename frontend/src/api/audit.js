/* 操作审计接口（/api/audit，T-7.6）：管理员可查全部账号，普通账号仅自己的操作 */
import { api, toQuery } from "./client";

export const auditLogs = (params = {}) =>
  api(`/api/audit${toQuery(params)}`);
