/* fn-finstat Service Worker（PWA 离线壳）
 *
 * 策略：
 * - /api/ 请求一律直连网络，绝不缓存（账单数据必须实时）
 * - 带内容哈希的 /assets/ 静态资源：缓存优先（文件名变更即缓存失效）
 * - 页面导航与其余同源 GET：网络优先，失败或 5xx 回退缓存（离线/后端重启可打开上次页面）
 * - 应用升级：SW_VERSION 变更后 activate 阶段清理旧缓存；index.html 每次网络优先取新
 *
 * 注意：SW_VERSION 由 vite.config.js 的 bump-sw-version 插件在每次构建时
 * 自动写入构建时间戳，无需手工维护；旧版本缓存在 activate 阶段自动清理。
 */
const SW_VERSION = "1789430157321";
const CACHE_NAME = `fn-finstat-v${SW_VERSION}`;

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)),
      );
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return; // 跨域请求不接管
  if (url.pathname.includes("/api/")) return; // 业务接口直连，不缓存

  const isHashedAsset = url.pathname.includes("/assets/");

  if (isHashedAsset) {
    // 缓存优先：内容哈希命名，命中即最新
    event.respondWith(
      caches.match(request).then(
        (cached) =>
          cached ||
          fetch(request).then((response) => {
            if (response.ok) {
              const clone = response.clone();
              caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
            }
            return response;
          }),
      ),
    );
    return;
  }

  // 导航与其余同源资源：网络优先，失败或 5xx 时回退缓存
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
          return response;
        }
        // 后端重启/网关 5xx：给用户缓存的可用页面而不是裸错误页
        return caches.match(request).then((cached) => cached || response);
      })
      .catch(() => caches.match(request)),
  );
});
