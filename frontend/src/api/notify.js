/* 通知中心接口（/api/notifications 与 /api/settings/notify） */
import { api, toQuery } from "./client";

/** 通知列表（广播 + 定向本人，含未读数），默认最近 50 条 */
export const listNotifications = (limit = 50) =>
  api(`/api/notifications${toQuery({ limit })}`);

/** 未读角标数（轻量轮询接口） */
export const getUnreadCount = () => api("/api/notifications/unread-count");

/** 全部已读（把本人已读水位线推到当前最大通知 id） */
export const markAllRead = () =>
  api("/api/notifications/read-all", { method: "POST" });

/** 通知配置视图（事件开关 + Webhook 掩码概要） */
export const getNotifyConfig = () => api("/api/settings/notify/config");

/** 保存通知配置（仅管理员）：events 为 {类型: 是否开启}；
 * webhook.url 传 null 表示保持已保存地址不变，空串表示清除 */
export const saveNotifyConfig = (payload) =>
  api("/api/settings/notify/config", {
    method: "PUT",
    body: JSON.stringify(payload),
  });

/** 发送测试推送（仅管理员）：url 缺省时后端用已保存地址测试 */
export const testNotifyWebhook = (payload) =>
  api("/api/settings/notify/webhook-test", {
    method: "POST",
    body: JSON.stringify(payload),
  });
