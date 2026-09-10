/* 主题状态：auto（跟随系统）/ light / dark，偏好持久化在 localStorage。
 * 首屏是否为暗色由 index.html 内联脚本先行判定，避免暗色用户看到浅色闪烁；
 * 本模块在 Vue 启动后接管状态并监听系统主题变化。 */
import { ref, watchEffect } from "vue";

const THEME_KEY = "fn-finstat-theme";
const MODES = ["auto", "light", "dark"];

const media = window.matchMedia("(prefers-color-scheme: dark)");

function readMode() {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (MODES.includes(saved)) return saved;
  } catch (e) {
    /* 隐私模式等 localStorage 不可用时退回 auto */
  }
  return "auto";
}

/** 用户选择的主题模式：auto / light / dark */
export const themeMode = ref(readMode());

/** 实际生效的是否暗色（auto 时跟随系统） */
export const isDark = ref(themeMode.value === "dark" || (themeMode.value === "auto" && media.matches));

watchEffect(() => {
  const mode = themeMode.value;
  try {
    localStorage.setItem(THEME_KEY, mode);
  } catch (e) {
    /* 写不进去也不影响本次会话 */
  }
  const dark = mode === "dark" || (mode === "auto" && media.matches);
  isDark.value = dark;
  document.documentElement.classList.toggle("dark", dark);
});

/* 系统主题变化时，auto 模式实时跟随；index.html 的内联脚本不监听，之后由这里接管 */
media.addEventListener("change", (e) => {
  if (themeMode.value === "auto") isDark.value = e.matches;
});

/* 其他标签页切换主题时同步本页 */
window.addEventListener("storage", (e) => {
  if (e.key === THEME_KEY) themeMode.value = readMode();
});

export function setTheme(mode) {
  themeMode.value = MODES.includes(mode) ? mode : "auto";
}

/** 循环切换：auto → light → dark → auto */
export function cycleTheme() {
  const i = MODES.indexOf(themeMode.value);
  setTheme(MODES[(i + 1) % MODES.length]);
}
