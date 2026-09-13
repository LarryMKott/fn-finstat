import { createApp } from "vue";
import App from "./App.vue";
import "./assets/style.css";
/* 主题模块在挂载前导入：它会立刻探测飞牛宿主主题并同步到 <html>，
 * 避免首屏用错配色（index.html 内联脚本已做同样判断，这里保证后续状态一致） */
import { syncThemeFromServer } from "./theme";
import { startFnosThemeWatch } from "./fnos";

createApp(App).mount("#app");

/* DOM 就绪后再挂探针并启动监听：飞牛在 iframe 内切主题时能即刻跟随 */
startFnosThemeWatch();
syncThemeFromServer();

/* PWA：生产构建才注册 Service Worker（开发模式热更新与 SW 缓存互相干扰）。
 * 相对路径注册，自动作用在接口前缀（如 /app/fn-finstat/）之下。 */
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("./sw.js").catch(() => {
      /* 注册失败（如非 HTTPS/localhost 环境）不影响应用功能 */
    });
  });
}
