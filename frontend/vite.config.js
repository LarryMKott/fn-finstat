import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 构建产物直接输出到 FastAPI 托管的静态目录 app/static：
// - base 使用统一网关前缀：fnOS 网关按 /app/fn-finstat/* 原样转发（不剥离前缀），
//   页面资源必须落在前缀之下；本地开发由后端同前缀双挂载保证行为一致
// - outDir 在项目根之外，必须显式 emptyOutDir
export default defineConfig({
  plugins: [vue()],
  base: "/app/fn-finstat/",
  build: {
    outDir: "../app/static",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/app/fn-finstat": "http://127.0.0.1:8090",
    },
  },
});
