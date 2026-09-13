/* 主题状态
 *
 * 三种用户可选模式：
 *   auto  —— 跟随宿主（默认）。优先飞牛 fnOS 的日间/夜间设置，探测不到时退回
 *            浏览器/操作系统的 prefers-color-scheme，并实时响应二者变化。
 *   light —— 始终浅色（显式指定，不再跟随）。
 *   dark  —— 始终深色（同上）。
 *
 * 关键点：auto 模式下「飞牛主题」优先于「操作系统主题」。因为本应用以 iframe
 * 嵌在飞牛桌面里，用户在飞牛里选的日间/夜间才代表当前使用环境的真实意图；
 * 浏览器自身的 prefers-color-scheme 在这个场景里只是兜底（探测不到宿主时）。
 *
 * 首屏是否为暗色由 index.html 内联脚本先行判定（同样按这个优先级），避免
 * 暗色用户看到一帧浅色闪烁；本模块在 Vue 启动后接管状态并持续监听变化。
 */
import { ref, watch, watchEffect } from "vue";
import { apiUrl } from "./api/client";
import { fnosTheme, startFnosThemeWatch, themeSource, pushFnosTheme } from "./fnos";

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

/** 实际生效的是否暗色 */
export const isDark = ref(false);

/** 当前是否真的跟随上了飞牛宿主（用于设置页提示，避免用户误以为没生效） */
export const followingFnos = ref(false);

/** 计算某模式下的最终暗色状态：auto 时飞牛主题优先，其次系统偏好 */
function resolveDark(mode) {
  if (mode === "dark") return true;
  if (mode === "light") return false;
  // auto：宿主主题优先（由 fnos.js 探测，探测不到时其值已回退为系统偏好）
  const host = fnosTheme.value;
  if (host === "dark") return true;
  if (host === "light") return false;
  return media.matches;
}

/** 把最终主题状态同步到 DOM：html.dark 类 + theme-color（浏览器 UI 配色） */
function syncDom(dark) {
  const root = document.documentElement;
  root.classList.toggle("dark", dark);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", dark ? "#12151a" : "#f5f2ec");
}

/* auto 模式才需要启动宿主探测；用户已锁定 light/dark 时省掉全部监听开销 */
if (themeMode.value === "auto") startFnosThemeWatch();

watchEffect(() => {
  const mode = themeMode.value;
  try {
    localStorage.setItem(THEME_KEY, mode);
  } catch (e) {
    /* 写不进去也不影响本次会话 */
  }

  if (mode === "auto") {
    startFnosThemeWatch(); // 从手动切回 auto 时补启探测
    followingFnos.value = themeSource.value === "fnos";
    syncThemeFromServer(); // 跨域 iframe 场景的后端兜底通道
  } else {
    followingFnos.value = false;
  }

  const dark = resolveDark(mode);
  isDark.value = dark;
  syncDom(dark);
});

/* 飞牛主题变化（storage / postMessage / 轮询任一触发）→ auto 模式实时跟随 */
watch(fnosTheme, () => {
  if (themeMode.value !== "auto") return;
  if (themeSource.value === "fnos") followingFnos.value = true;
  isDark.value = resolveDark("auto");
  syncDom(isDark.value);
});

/* 系统偏好变化：auto 模式下且飞牛主题未命中时实时跟随 */
media.addEventListener("change", () => {
  if (themeMode.value !== "auto") return;
  isDark.value = resolveDark("auto");
  syncDom(isDark.value);
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

/** 后端兜底通道：/api/settings/about 会带上宿主透传的主题（跨域 iframe 场景） */
export async function syncThemeFromServer() {
  if (themeMode.value !== "auto") return;
  try {
    const res = await fetch(apiUrl("/api/settings/about"), {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return;
    const body = await res.json();
    const data = body && typeof body === "object" && "data" in body ? body.data : body;
    if (!data || !data.fnos_theme) return;
    if (pushFnosTheme(data.fnos_theme)) {
      followingFnos.value = true;
      isDark.value = resolveDark("auto");
      syncDom(isDark.value);
    }
  } catch (e) {
    /* 后端不可用时静默跳过，前端探测链路仍然有效 */
  }
}
