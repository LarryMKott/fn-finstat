/* 后端接口访问封装 */
const BASE = import.meta.env.BASE_URL || "/";

export async function api(path, options = {}) {
  const opts = { ...options };
  if (opts.body && typeof opts.body === "string") {
    opts.headers = { "Content-Type": "application/json", ...opts.headers };
  }
  /* path 以 / 开头时挂在网关前缀下（BASE_URL 形如 /app/fn-finstat/） */
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
