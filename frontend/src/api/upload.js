/* 账单导入接口：平台上传（/api/upload/*）与 NAS 目录导入（/api/nas/*） */
import { api, toQuery } from "./client";

/** 上传平台账单文件；kind 为平台标识（wechat/alipay/jd/unionpay） */
export const uploadBillFile = (kind, formData) =>
  api(`/api/upload/${kind}`, { method: "POST", body: formData, timeout: 120_000 });

export const nasGetConfig = () => api("/api/nas/config");
export const nasSaveConfig = (import_dir) =>
  api("/api/nas/config", { method: "PUT", body: JSON.stringify({ import_dir }) });
export const nasListFiles = (path = "") => api(`/api/nas/files${toQuery({ path })}`);
export const nasImport = (path) =>
  api("/api/nas/import", { method: "POST", body: JSON.stringify({ path }), timeout: 120_000 });

/** 查询当前用户在飞牛上已授权给本应用的账单目录（trim 网关） */
export const nasGetAuthorization = () => api("/api/nas/authorization");
/** 检查若干账单目录内路径的可读/可写/可删权限（飞牛环境） */
export const nasCheckAcl = (paths) =>
  api("/api/nas/authorization/check-acl", {
    method: "POST",
    body: JSON.stringify({ paths }),
  });
