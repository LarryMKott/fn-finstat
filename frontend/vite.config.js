import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 构建产物直接输出到 FastAPI 托管的静态目录 app/static：
// - base 使用相对路径：接口地址前缀由向导参数 wizard_api_base_path 控制（运行时可变），
//   前端从页面 URL 自动推导接口地址，因此不能在构建期写死绝对 base
// - outDir 在项目根之外，必须显式 emptyOutDir
export default defineConfig({
  plugins: [vue()],
  base: "./",
  build: {
    outDir: "../app/static",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8090",
      "/app/fn-finstat": "http://127.0.0.1:8090",
    },
  },
});
