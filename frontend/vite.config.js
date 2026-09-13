import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * Service Worker 版本自动化：构建完成后把产物 sw.js 的 SW_VERSION 改写为
 * 构建时间戳。此前依赖开发者每次构建后手工递增，漏改会导致旧构建的
 * /assets 资源在浏览器缓存里永久堆积（见 public/sw.js 头部说明）。
 */
function bumpSwVersion() {
  return {
    name: "bump-sw-version",
    closeBundle() {
      // import.meta.dirname 而非 __dirname：后者不受 vite 的 native configLoader 支持
      const swFile = resolve(import.meta.dirname, "../app/static/sw.js");
      try {
        const content = readFileSync(swFile, "utf-8");
        const stamped = content.replace(
          /const SW_VERSION = "[^"]*";|const SW_VERSION = \d+;/,
          `const SW_VERSION = "${Date.now()}";`,
        );
        if (stamped !== content) writeFileSync(swFile, stamped);
      } catch (e) {
        // 版本号改写失败不影响构建产物本身，仅告警提示手工处理
        console.warn("[bump-sw-version] 未能更新 sw.js 版本号：", e.message);
      }
    },
  };
}

// 构建产物直接输出到 FastAPI 托管的静态目录 app/static：
// - base 使用相对路径：接口地址前缀由向导参数 wizard_api_base_path 控制（运行时可变），
//   前端从页面 URL 自动推导接口地址，因此不能在构建期写死绝对 base
// - outDir 在项目根之外，必须显式 emptyOutDir
export default defineConfig({
  plugins: [vue(), bumpSwVersion()],
  base: "./",
  build: {
    outDir: "../app/static",
    emptyOutDir: true,
    // echarts 整库拆为一个 chunk（含 lib/ 下的核心实现），约 628KB：
    // 它是升级频率低的第三方库，独立 chunk 便于浏览器长缓存，同时让主包
    // 只留应用代码（约 183KB）。阈值按其实际体积设定，避免无意义告警。
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        // echarts 体积大且升级频率低，拆为独立 chunk 利于浏览器长缓存；
        // vite 8.x (rolldown) 要求 manualChunks 为函数，对象形式不再接受。
        // 用目录前缀而非逐个列举子包（core/charts/components/renderers），
        // 这样 echarts 新增子包时无需回来改配置。
        manualChunks(id) {
          if (id.includes("/node_modules/echarts/")) {
            return "echarts";
          }
          return null;
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8090",
      "/app/fn-finstat": "http://127.0.0.1:8090",
    },
  },
});
