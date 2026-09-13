import globals from "globals";
import pluginVue from "eslint-plugin-vue";
import skipFormatting from "@vue/eslint-config-prettier/skip-formatting";

export default [
  { name: "app/file-environment", languageOptions: { globals: { ...globals.browser } } },
  ...pluginVue.configs["flat/recommended"],
  skipFormatting,
  {
    name: "app/custom-rules",
    rules: {
      "vue/multi-word-component-names": "off", // 面板类单词组件名（Dashboard 等）为本项目惯例
      "vue/max-attributes-per-line": "off",
      "vue/singleline-html-element-content-newline": "off",
      "vue/html-self-closing": "off",
      // 兜底 catch（如 localStorage 隐私模式）为有意静默，不做未用参数告警
      "no-unused-vars": ["error", { argsIgnorePattern: "^_", caughtErrors: "none" }],
    },
  },
  {
    name: "app/ignores",
    ignores: ["dist/**", "node_modules/**", "public/sw.js", "../app/static/**"],
  },
];
