/* 后端接口访问封装 */
/* 接口地址前缀在运行时从页面地址推导（向导参数 wizard_api_base_path 可改，无需重新构建）：
   页面在 /app/fn-finstat/ 下 -> 接口为 /app/fn-finstat/api/*；页面在 / 下 -> /api/* */
const BASE = (() => {
  const path = window.location.pathname.replace(/index\.html$/, "");
  return path.endsWith("/") ? path : `${path}/`;
})();

/* 拼接接口地址（下载链接等需要原始 URL 的场景使用） */
export function apiUrl(path) {
  return path.startsWith("/") ? BASE.replace(/\/$/, "") + path : path;
}

export async function api(path, options = {}) {
  const opts = { ...options };
  if (opts.body && typeof opts.body === "string") {
    opts.headers = { "Content-Type": "application/json", ...opts.headers };
  }
  const res = await fetch(apiUrl(path), opts);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      if (j.detail) {
        // FastAPI 422 的 detail 是校验错误数组，拼出可读信息而不是 "[object Object]"
        detail = Array.isArray(j.detail)
          ? j.detail.map((d) => d?.msg || JSON.stringify(d)).join("；")
          : typeof j.detail === "string"
            ? j.detail
            : JSON.stringify(j.detail);
      }
    } catch (e) {
      /* 非 JSON 响应，保留 statusText */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}
