/* 统一请求封装：地址推导、超时控制、响应解包与错误码
 *
 * 后端统一响应结构（见 app/core/handlers.py）：
 *   成功  { code: 0, msg: "ok", data: <业务数据> }
 *   失败  { code: <错误码>, msg: <用户可读信息>, data: null }
 * api() 统一解包：2xx 返回 data（204 返回 null），非 2xx 抛 Error（message=msg，err.code=错误码），
 * 组件层 catch 后 toast(err.message) 即可，无需关心响应结构。
 *
 * 接口地址前缀在运行时从页面地址推导（向导参数 wizard_api_base_path 可改，无需重新构建）：
 *   页面在 /app/fn-finstat/ 下 -> 接口为 /app/fn-finstat/api/*；页面在 / 下 -> /api/*
 */

const BASE = (() => {
  if (typeof window === "undefined") return "/"; // 非浏览器环境（如单测）兜底
  const path = window.location.pathname.replace(/index\.html$/, "");
  return path.endsWith("/") ? path : `${path}/`;
})();

/** 默认请求超时（毫秒）：普通查询足够；上传/导入、AI 等长请求在调用处放宽 */
export const DEFAULT_TIMEOUT = 30_000;

/* 拼接接口地址（下载链接等需要原始 URL 的场景使用） */
export function apiUrl(path) {
  return path.startsWith("/") ? BASE.replace(/\/$/, "") + path : path;
}

/* 参数对象 → 查询串：跳过 null/undefined/空串，避免后端收到空条件 */
export function toQuery(params = {}) {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    qs.set(key, value);
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

/* 合并「外部 signal」与「超时」为单一 signal：任一触发即中断。
 * 不直接用 AbortSignal.timeout —— 旧运行环境（部分内嵌 WebView）没有该 API，
 * 且直接赋值 opts.signal 会把调用方传入的外部 signal 覆盖掉。
 * 超时 abort 的 reason 用 TimeoutError DOMException，与原生 API 的错误形态一致，
 * api() 据此翻译成「请求超时」文案。 */
function buildTimeoutSignal(external, timeout) {
  if (!(timeout > 0) && !external) return { signal: undefined, cleanup: () => {} };
  const ctrl = new AbortController();
  let timer = null;
  if (timeout > 0) {
    timer = setTimeout(() => {
      ctrl.abort(new DOMException("signal timed out", "TimeoutError"));
    }, timeout);
  }
  const onExternalAbort = () => ctrl.abort(external.reason);
  if (external) {
    if (external.aborted) ctrl.abort(external.reason);
    else external.addEventListener("abort", onExternalAbort, { once: true });
  }
  return {
    signal: ctrl.signal,
    cleanup: () => {
      if (timer) clearTimeout(timer);
      if (external) external.removeEventListener("abort", onExternalAbort);
    },
  };
}

export async function api(path, options = {}) {
  const { timeout = DEFAULT_TIMEOUT, ...opts } = { ...options };
  if (opts.body && typeof opts.body === "string") {
    opts.headers = { "Content-Type": "application/json", ...opts.headers };
  }
  const { signal, cleanup } = buildTimeoutSignal(opts.signal, timeout);
  if (signal) opts.signal = signal;
  let res;
  try {
    res = await fetch(apiUrl(path), opts);
  } catch (err) {
    if (err?.name === "TimeoutError") throw new Error("请求超时，请稍后重试");
    throw err;
  } finally {
    cleanup(); // 请求已落定（成功或抛错），清掉超时计时器防泄漏
  }
  if (!res.ok) {
    const err = new Error(res.statusText || `请求失败（HTTP ${res.status}）`);
    try {
      const body = await res.json();
      if (body && typeof body === "object") {
        err.code = body.code;
        if (body.msg) err.message = body.msg;
        // 兼容旧的 {detail} 错误结构（历史版本后端/代理层）
        else if (body.detail) {
          err.message = Array.isArray(body.detail)
            ? body.detail.map((d) => d?.msg || JSON.stringify(d)).join("；")
            : typeof body.detail === "string"
              ? body.detail
              : JSON.stringify(body.detail);
        }
      }
    } catch {
      /* 非 JSON 响应，保留 statusText */
    }
    throw err;
  }
  if (res.status === 204) return null;
  const body = await res.json();
  if (body && typeof body === "object" && "code" in body && "data" in body) {
    if (body.code !== 0) {
      const err = new Error(body.msg || "请求失败");
      err.code = body.code;
      throw err;
    }
    return body.data;
  }
  return body; // 兜底：未包装的裸响应
}
