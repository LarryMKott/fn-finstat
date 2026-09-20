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

/* ---- 配色主题（与日间/夜间正交的第二个维度）----
 * pine 即 tokens.css 的默认配色（无 data-theme 属性）；其余主题各自提供
 * 日间+夜间两套（styles/themes.css），只覆盖品牌与中性层，收支语义全局共享。
 * swatch 供设置页色板圆点展示其他主题的主色（当前主题色随 CSS 变量走）。 */
const ACCENT_KEY = "fn-finstat-accent";
export const ACCENTS = [
  { value: "pine", label: "松烟", swatch: "#2d5443" },
  { value: "ocean", label: "沧蓝", swatch: "#33608f" },
  { value: "violet", label: "紫棠", swatch: "#6b5493" },
  { value: "rose", label: "绯樱", swatch: "#b0486e" },
  { value: "coffee", label: "焦糖", swatch: "#7d5030" },
  { value: "slate", label: "石墨", swatch: "#4a5a6e" },
];
const ACCENT_VALUES = ACCENTS.map((a) => a.value);

function readAccent() {
  try {
    const saved = localStorage.getItem(ACCENT_KEY);
    if (ACCENT_VALUES.includes(saved)) return saved;
  } catch (e) {
    /* localStorage 不可用时退回默认配色 */
  }
  return "pine";
}

/** 用户选择的配色主题（pine/ocean/violet/rose/coffee/slate） */
export const themeAccent = ref(readAccent());

export function setAccent(value) {
  if (ACCENT_VALUES.includes(value)) themeAccent.value = value;
}

/** 把配色主题同步到 DOM：pine 移除属性走默认令牌，其余设 data-theme */
function syncAccent() {
  const root = document.documentElement;
  if (themeAccent.value === "pine") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", themeAccent.value);
}

/** 浏览器 UI 配色（theme-color）取自当前生效的 --color-bg 令牌，
 *  明暗与配色任一变化后都重读一次；读取失败保留回退值 */
function syncMetaThemeColor() {
  const meta = document.querySelector('meta[name="theme-color"]');
  if (!meta) return;
  const bg = getComputedStyle(document.documentElement).getPropertyValue("--color-bg").trim();
  if (bg) meta.setAttribute("content", bg);
}

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
  }

  const dark = resolveDark(mode);
  isDark.value = dark;
  syncDom(dark);
  /* 配色影响背景底色，明暗切换后重读一次浏览器 UI 配色 */
  syncMetaThemeColor();
});

/* 配色主题：持久化 + DOM 同步；切换后同样刷新浏览器 UI 配色。
 * 独立于明暗 effect：两个维度互不依赖，各自变化各自生效 */
watchEffect(() => {
  const accent = themeAccent.value;
  try {
    localStorage.setItem(ACCENT_KEY, accent);
  } catch (e) {
    /* 写不进去也不影响本次会话 */
  }
  syncAccent();
  syncMetaThemeColor();
});

/* followingFnos 跟随状态：独立于主 effect。不能在主 watchEffect 里读 themeSource
 * —— 它会被收进依赖，syncThemeFromServer 命中后 pushFnosTheme 回写 themeSource
 * 又触发 effect 重跑，造成 /api/settings/about 重复请求 */
watch(
  [themeMode, themeSource],
  ([mode, source]) => {
    followingFnos.value = mode === "auto" && source === "fnos";
  },
  { immediate: true },
);

/* 后端兜底通道（跨域 iframe 场景）只在「切到 auto」（含首启）时请求一次，
 * themeSource 变化不重发 */
watch(
  themeMode,
  (mode) => {
    if (mode === "auto") syncThemeFromServer();
  },
  { immediate: true },
);

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

/* 其他标签页切换主题时同步本页（明暗模式与配色两个维度都监听） */
window.addEventListener("storage", (e) => {
  if (e.key === THEME_KEY) themeMode.value = readMode();
  if (e.key === ACCENT_KEY) themeAccent.value = readAccent();
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
