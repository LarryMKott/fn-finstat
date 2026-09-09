/* 后端接口访问封装 */
/* 接口地址前缀在运行时从页面地址推导（向导参数 wizard_api_base_path 可改，无需重新构建）：
   页面在 /app/fn-finstat/ 下 -> 接口为 /app/fn-finstat/api/*；页面在 / 下 -> /api/* */
const BASE = (() => {
  const path = window.location.pathname.replace(/index\.html$/, "");
  return path.endsWith("/") ? path : `${path}/`;
})();

export async function api(path, options = {}) {
  const opts = { ...options };
  if (opts.body && typeof opts.body === "string") {
    opts.headers = { "Content-Type": "application/json", ...opts.headers };
  }
  const url = path.startsWith("/") ? BASE.replace(/\/$/, "") + path : path;
  const res = await fetch(url, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      if (j.detail) detail = j.detail;
    } catch (e) {
      /* 非 JSON 响应，保留 statusText */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}
