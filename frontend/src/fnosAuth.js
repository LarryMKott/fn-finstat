/* 飞牛用户个人授权目录（trim.file.userAccess / userAcl）
 *
 * 应用 v0.4+ 支持：用户在飞牛里把账单所在目录授权给本应用，应用后端用
 * trim 网关获取「当前用户已授权目录」，前端可在导入页感知并提示申请。
 *
 * 设计目标（与 fnos.js 同源）：
 * - 复用同一套 SDK 加载守卫（动态 import + 超时 + iframe 内限定）
 * - 失败/不可用一律静默降级，不弹错；导入页感知到 available=false 即落入旧路径
 * - 复用 `nasGetAuthorization`/`nasCheckAcl` 作为权威来源，避免前后端双标准
 * - 申请授权动作（pickUserFile）仅在用户点击按钮时触发，遵守文档里的
 *   "必须用户点击触发"约束（不在页面加载或定时器里自动开）
 *
 * ⚠️ 多账号注意：sdkSingleton 一旦建立就与当时的宿主用户上下文绑定，且 SDK
 * 未提供"切换用户"事件。若将来要支持飞牛多账号热切换，需要在宿主发出用户
 * 变更时把 sdkSingleton 置空重建（当前无此事件，故切账号请刷新页面）。
 */

import { ref } from "vue";
import {
  nasGetAuthorization,
  nasCheckAcl,
} from "./api/upload";

/** 用户账单目录授权状态（来自后端 /api/nas/authorization，已结构化） */
export const authorizationStatus = ref(null);

/** 当前用户已授权目录列表（同步 ref，避免组件反复 await） */
export const authorizedFolders = ref([]);

/** 是否处于飞牛环境（available && 至少有一个授权目录） */
export const hasAuthorizedFolders = () =>
  !!authorizationStatus.value?.available && authorizedFolders.value.length > 0;

let sdkSingleton = null;

/** 是否在 iframe 宿主环境内（与 fnos.js 逻辑对齐；独立 web / 本地部署跳过） */
function inIframe() {
  try {
    return typeof window !== "undefined" && window.parent !== window;
  } catch (e) {
    return false;
  }
}

/** 给任意 Promise 套一层超时：SDK 在无响应宿主下会永久挂起，必须能脱身 */
function withTimeout(promise, ms, fallback) {
  return new Promise((resolve) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      resolve(fallback);
    }, ms);
    Promise.resolve(promise).then(
      (value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(value);
      },
      () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(fallback); // 失败一律走 fallback
      },
    );
  });
}

/** 尝试获取 SDK 实例（动态 import 触发代码分割） */
async function getSdk() {
  if (sdkSingleton) return sdkSingleton;
  if (!inIframe()) return null;
  try {
    const mod = await withTimeout(import("@trimjs/web-app"), 2500, null);
    if (!mod) return null;
    const app = new mod.TrimApp();
    sdkSingleton = app;
    return app;
  } catch (e) {
    return null;
  }
}

/** 拉取后端授权状态，同步更新 ref；失败/不可用容灾为空数组 */
export async function refreshAuthorizationStatus() {
  try {
    const status = await nasGetAuthorization();
    authorizationStatus.value = status;
    // 官方接口 data 是结构化的「应用级显示」，以它为准；trim 网关无法用时为空
    authorizedFolders.value = Array.isArray(status?.folders) ? status.folders : [];
  } catch (err) {
    // 后端调用失败：保留旧值，仅在尚未有任何状态时设置兜底
    if (!authorizationStatus.value) {
      authorizationStatus.value = {
        available: false,
        authorized: false,
        folders: [],
        reason: err?.message || "无法获取授权状态",
        uid: 0,
      };
      authorizedFolders.value = [];
    }
  }
  return authorizationStatus.value;
}

/** 让用户选择并授权一个目录（飞牛 SDK pickUserFile）
 *
 * 返回 Promise<{success: boolean, paths: string[], reason?: string}>：
 * - success=true 且 paths 非空：授权成功
 * - success=false：用户取消、未启用 trim、scope 未声明等，统一 reason 说明
 *
 * 仅可在用户点击按钮时调用（文档要求：不在定时器/页面加载里自动开新窗口）。
 */
export async function pickUserDirectory() {
  const app = await getSdk();
  if (!app) {
    return {
      success: false,
      paths: [],
      reason: "当前不在飞牛桌面环境，无法打开授权选择器",
    };
  }

  let result;
  try {
    result = await withTimeout(
      app.pickUserFile({
        directory: true,
        title: "选择账单所在目录",
        okText: "确认授权",
        sidebarGroup: ["myFiles", "otherShare", "favorites"],
      }),
      10_000, // 用户点击 → 长时间等待属于正常
      null,
    );
  } catch (e) {
    return { success: false, paths: [], reason: `授权弹窗失败：${e?.message || e}` };
  }

  if (!result) {
    return { success: false, paths: [], reason: "授权弹窗超时未返回" };
  }
  if (result.code !== 0) {
    return {
      success: false,
      paths: [],
      reason: `飞牛返回非 0：${result.msg || "(无错误消息)"}`,
    };
  }
  const paths = Array.isArray(result.data) ? result.data : [];
  // 选择动作已成功，这里只是一次"让 UI 立刻反映"的后端视图刷新：
  // 它失败不代表授权失败，静默掉避免调用方把重试错误误报成"授权异常"
  // （下一次用户点「刷新状态」或重新进入页面时会自然重新拉取）。
  try {
    await refreshAuthorizationStatus();
  } catch (e) {
    console.warn("[fnosAuth] 授权成功但刷新后端视图失败：", e);
  }
  return { success: paths.length > 0, paths, reason: paths.length ? "" : "未选择任何目录" };
}

/** 对账单目录内的一组路径做权限检查（前端二次校验）。
 * 主要给"批量禁用未授权文件"用，本地/不可用时一律返回空对象（=调用方按全权放行）。 */
export async function checkPathsAcl(paths) {
  if (!Array.isArray(paths) || paths.length === 0) return {};
  try {
    return await nasCheckAcl(paths);
  } catch (err) {
    return {};
  }
}
