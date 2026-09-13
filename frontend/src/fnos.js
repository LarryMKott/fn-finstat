/* 飞牛 fnOS 宿主主题探测与实时订阅
 *
 * 背景：本应用在飞牛桌面中以 iframe 形式嵌入（app/ui/config 的 fn-finstat.main 是
 * type=iframe 入口）。飞牛把「日间/夜间」选择存在 localStorage 的 fnos-theme-mode，
 * 取值为数字字符串（'10' = 日间 / 亮色，'20' = 夜间 / 暗色）。但 iframe 三方应用
 * 无法保证读到该键——同源时可直接读，跨域（fn Connect 子域、独立端口）则被隔离。
 *
 * 因此这里做「多源探测 + 实时订阅」，按可靠性从高到低依次尝试，任一命中即生效：
 *
 *   0. 官方 JS SDK    @trimjs/web-app 的 getPlatformConfig().theme + $on('os/theme')
 *                     这是飞牛官方开放能力，语义最准、能收到实时推送（推荐通道）
 *   1. URL 查询参数   ?fnos-theme=dark | light    始终可用，宿主/跳转页可直接传
 *   2. 同源宿主读取   localStorage["fnos-theme-mode"]（iframe 内与父窗口都试）
 *   3. 宿主探针       iframe 内注入隐藏探针元素，用 CSS 变量回读父级主题（同源）
 *   4. postMessage    宿主或中介页主动 postMessage 主题（跨域友好）
 *   5. 后端透传       /api/settings/about 的 fnos_theme 字段（跨域且无 JS SDK 时）
 *   6. media query    (prefers-color-scheme: dark) —— 常规浏览器 / PWA 兜底
 *
 * 实时性：0 靠 SDK 事件推送，1 靠 popstate/hashchange 重读，2/3 靠 storage 事件 +
 * 定时轮询，4 靠 message 事件，6 靠 matchMedia change。
 *
 * ⚠️ 关于官方 SDK 的两条实测结论（务必保留守卫，勿简化）：
 *   1. 在独立浏览器页面里访问 SDK 会抛
 *      "Host bridge is not available outside iframe or app runtime"
 *      ——由 sdk.isStandaloneWeb === true 判定，故非 iframe 环境直接跳过。
 *   2. 在 iframe 里但宿主不响应握手时，getPlatformConfig() 既不 resolve 也不
 *      reject（实测 3s 无结果，握手超时为 1500ms 但整体 Promise 会滞留）。
 *      ——故必须加超时竞速，且绝不能让首屏路径 await 它。
 */
import { ref } from "vue";

/** 宿主主题：'light' | 'dark' | null（null = 未探测到飞牛环境，退回系统偏好） */
export const fnosTheme = ref(null);

/** 当前主题来源，用于界面提示，便于用户判断是否真的跟上了飞牛 */
export const themeSource = ref("system");

/** 当前是否接入了官方 SDK 通道（能在设置页体现「官方通道已就绪」） */
export const sdkHosted = ref(false);

/** 飞牛 fnos-theme-mode 的取值映射（10 = 日间 / 亮色，20 = 夜间 / 暗色） */
const FNOS_MODE = { "10": "light", "20": "dark" };

/** 归一化外部传入的主题值：兼容 light/dark、日间/夜间、10/20、true 等写法 */
function normalizeTheme(raw) {
  if (raw === null || raw === undefined) return null;
  const v = String(raw).trim().toLowerCase();
  if (!v) return null;
  if (v in FNOS_MODE) return FNOS_MODE[v];
  if (v === "dark" || v === "night" || v === "true" || v === "2") return "dark";
  if (v === "light" || v === "day" || v === "false" || v === "1") return "light";
  return null;
}

/* ---------- 来源 0：官方 JS SDK（@trimjs/web-app） ---------- */

/* SDK 只在「iframe 宿主环境」里才有意义：
 *   - 独立浏览器页面（window.parent === window）会抛 Host bridge 错误，跳过
 *   - 若首页本身不在 iframe 内，说明不是被飞牛桌面嵌入，同样跳过
 * 注意不可静态 import：该包在模块顶层就访问 window / navigator。 */
function inIframe() {
  try {
    return typeof window !== "undefined" && window.parent !== window;
  } catch (e) {
    return false; // 跨域访问 window.parent 的某些属性会抛，但 === 比较本身安全
  }
}

/** 给任意 Promise 套一层超时：SDK 在无响应宿主下会永久挂起，必须能脱身 */
function withTimeout(promise, ms, fallback) {
  return new Promise((resolve) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      resolve(fallback);
    }, ms);
    Promise.resolve(promise).then(
      (value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(value);
      },
      () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(fallback); // 失败一律走 fallback，绝不把异常抛给调用方
      },
    );
  });
}

/** SDK 实例与订阅状态（惰性创建，全局唯一） */
let sdk = null;
let sdkThemeHandler = null;
let sdkLangHandler = null;

/** 宿主界面语言（'zh-CN' / 'en-US' …），供将来做界面语言跟随（预留，暂无消费方） */
const fnosLanguage = ref(null);

/** 主题来自 SDK 时标记为飞牛来源；SDK 值域官方为 'light' | 'dark' */
function applySdkTheme(raw) {
  const theme = normalizeTheme(raw);
  if (!theme) return false;
  pushedTheme = theme;
  sdkControlled = true;
  apply();
  return true;
}

function applySdkLanguage(raw) {
  if (!raw) return false;
  fnosLanguage.value = String(raw);
  return true;
}

/** 尝试通过官方 SDK 获取主题并订阅变化；失败静默降级，不影响其余链路 */
async function startSdkThemeWatch() {
  if (!inIframe() || sdk) return;
  try {
    const mod = await import("@trimjs/web-app");
    const app = new mod.TrimApp();
    sdk = app;

    // 初始主题：等宿主握手，但最多等 2.5s，超时即放弃本次（可能稍后才就绪）
    const config = await withTimeout(app.getPlatformConfig(), 2500, null);
    if (config) {
      if (config.theme) applySdkTheme(config.theme);
      if (config.language) applySdkLanguage(config.language);
    }

    // 实时订阅：官方要求 isWeb === true && isStandaloneWeb === false
    if (app.isWeb === true && app.isStandaloneWeb === false && typeof app.$on === "function") {
      sdkThemeHandler = (theme) => applySdkTheme(theme);
      await withTimeout(app.$on("os/theme", sdkThemeHandler), 2500, null);
      sdkHosted.value = true;

      // 语言变化同属「页面交互」开放能力，一并订阅（界面本身中文为主，暂只记录）
      sdkLangHandler = (language) => applySdkLanguage(language);
      await withTimeout(app.$on("os/language", sdkLangHandler), 2500, null);
    }
  } catch (e) {
    // 宿主不支持 / 版本过低 / 桥接不可用：保持 sdk = null，由其余来源接管
    sdk = null;
    sdkThemeHandler = null;
    sdkLangHandler = null;
    sdkHosted.value = false;
  }
}

/* ---------- 来源 1：URL 查询参数（宿主可显式指定） ---------- */

const THEME_PARAMS = ["fnos-theme", "theme", "colorScheme", "color-scheme"];

function readThemeFromUrl() {
  let search;
  try {
    search = new URLSearchParams(window.location.search);
  } catch (e) {
    return null;
  }
  for (const key of THEME_PARAMS) {
    const found = normalizeTheme(search.get(key));
    if (found) return found;
  }
  return null;
}

/* ---------- 来源 2：同源宿主 localStorage ---------- */

function readThemeFromStorage(win) {
  // 父级隔离或未授权时会抛 SecurityError，调用方按「读不到」处理
  try {
    return normalizeTheme(win.localStorage.getItem("fnos-theme-mode"));
  } catch (e) {
    return null;
  }
}

function readThemeFromHosts() {
  if (typeof window === "undefined") return null;
  const self = readThemeFromStorage(window);
  if (self) return self;
  // iframe 内若同源，父窗口的 localStorage 与自身是同一份；显式再读一次覆盖
  // 「应用部署在子路径、宿主在顶层」这类仍同源的场景
  try {
    if (window.parent && window.parent !== window) {
      const parent = readThemeFromStorage(window.parent);
      if (parent) return parent;
    }
  } catch (e) {
    /* 跨域：静默跳过，交给后续来源 */
  }
  return null;
}

/* ---------- 来源 3：宿主探针（同源时回读宿主解析后的实际背景色） ---------- */

const PROBE_ID = "fn-finstat-theme-probe";

function readThemeFromProbe(win) {
  try {
    const w = win || window;
    // 读 html 元素的实际背景：宿主主题若作用在文档上，这里能拿到；跨域抛错则跳过
    const bg = w.getComputedStyle(w.document.documentElement).backgroundColor;
    if (!bg || bg === "rgba(0, 0, 0, 0)" || bg === "transparent") return null;
    const m = bg.match(/\d+(\.\d+)?/g);
    if (!m || m.length < 3) return null;
    // 宿主主题色经继承到达探针；亮度低于中位判为夜间
    const [r, g, b] = m.map(Number);
    const luma = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    return luma < 110 ? "dark" : "light";
  } catch (e) {
    return null;
  }
}

/* 探针元素仅用于让宿主主题背景色可被探测（挂在与宿主同源的文档上时有效），
 * 同时也是 CSS 侧读取宿主主题的锚点；不参与布局、不可见、不响应交互 */
function mountProbe() {
  try {
    if (!document.body || document.getElementById(PROBE_ID)) return;
    const el = document.createElement("div");
    el.id = PROBE_ID;
    el.setAttribute("aria-hidden", "true");
    el.style.cssText = [
      "position:absolute",
      "top:0",
      "left:0",
      "width:0",
      "height:0",
      "overflow:hidden",
      "pointer-events:none",
      "visibility:hidden",
    ].join(";");
    document.body.appendChild(el);
  } catch (e) {
    /* DOM 未就绪时忽略：探测的其余来源不依赖探针 */
  }
}

/* ---------- 来源 4：postMessage（跨域宿主主动推送） ---------- */

const MESSAGE_KEYS = ["fnos-theme", "fnosTheme", "theme", "colorScheme", "color-scheme"];

function readThemeFromMessage(event) {
  const data = event && event.data;
  if (!data) return null;
  if (typeof data === "string") return normalizeTheme(data);
  if (typeof data === "object") {
    for (const key of MESSAGE_KEYS) {
      const found = normalizeTheme(data[key]);
      if (found) return found;
    }
  }
  return null;
}

/* ---------- 来源 5：系统偏好 ---------- */

const media =
  typeof window !== "undefined" && window.matchMedia
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;

function readThemeFromMedia() {
  return media && media.matches ? "dark" : "light";
}

/* ---------- 汇总探测 ---------- */

/** 探测顺序即优先级；命中第一个非 null 值即返回。
 *  note: 抛错风险已由各 read* 内部消化，这里只负责串起优先级。 */
function detect() {
  // 同源宿主读到的 fnos-theme-mode 最可信：它就是飞牛自己的主题开关值
  const host = readThemeFromHosts();
  if (host) return { theme: host, source: "fnos" };

  // URL 参数次之：显式传入即视为宿主意图（也覆盖 localStorage 不可读的场景）
  const url = readThemeFromUrl();
  if (url) return { theme: url, source: "fnos" };

  const probe = readThemeFromProbe(window);
  if (probe) return { theme: probe, source: "fnos" };
  try {
    if (window.parent && window.parent !== window) {
      const parentProbe = readThemeFromProbe(window.parent);
      if (parentProbe) return { theme: parentProbe, source: "fnos" };
    }
  } catch (e) {
    /* 跨域：静默跳过 */
  }

  return { theme: readThemeFromMedia(), source: "system" };
}

/** 记录 postMessage / 后端接口上报的宿主主题（优先级最高：宿主主动声明最可信） */
let pushedTheme = null;

/** 主题是否由官方 SDK 接管：SDK 推送即权威值，其余来源不应覆盖它 */
let sdkControlled = false;

/** 宿主主动声明主题：postMessage、或后端 X-Trim-Theme 透传，均为跨域场景的可靠通道 */
export function pushFnosTheme(raw) {
  const theme = normalizeTheme(raw);
  if (!theme) return false;
  // 官方 SDK 已给出权威值且仍在推送时，不被次级通道覆盖（避免跨域返回值抖动）
  if (sdkControlled && theme !== pushedTheme) return false;
  pushedTheme = theme;
  apply();
  return true;
}

function apply() {
  if (pushedTheme) {
    fnosTheme.value = pushedTheme;
    themeSource.value = "fnos";
    return;
  }
  const { theme, source } = detect();
  fnosTheme.value = theme;
  themeSource.value = source;
}

/* ---------- 生命周期 ---------- */

let started = false;

function onStorage(event) {
  // 飞牛切换主题时会改写 fnos-theme-mode，storage 事件即时通知同源页面；
  // 但 cookie/主题类切换可能不带 key，因此 key 为 null 时也重探一次
  if (!event) return;
  if (event.key === null || event.key === "fnos-theme-mode") {
    // 宿主换了主题：SDK 会走自己的事件通道推送，这里让出控制权等它；无 SDK 时以探测为准
    if (sdkControlled) return;
    pushedTheme = null;
    apply();
  }
}

function onMessage(event) {
  /* 仅接受真正的父窗口发来的消息：任意第三方页面 postMessage 不得改写主题 */
  if (event.source !== window.parent) return;
  const theme = readThemeFromMessage(event);
  if (!theme) return;
  pushFnosTheme(theme);
}

export function startFnosThemeWatch() {
  if (started || typeof window === "undefined") return;
  started = true;

  mountProbe();
  apply();

  window.addEventListener("storage", onStorage);
  window.addEventListener("message", onMessage);
  window.addEventListener("popstate", apply);
  window.addEventListener("hashchange", apply);
  window.addEventListener("focus", apply);
  // 跨域宿主无法用 storage 事件通知，用低频轮询兜底（页面可见时才探测，开销可忽略）
  window.setInterval(() => {
    if (!document.hidden) apply();
  }, 4000);

  if (media && media.addEventListener) media.addEventListener("change", apply);

  // 官方 SDK 通道：异步尝试，成功则升级为实时推送；失败静默保持上述降级链路
  startSdkThemeWatch();
}

/* 首次导入即探测一次：此时 DOM 可能尚未就绪，探针在 startFnosThemeWatch 里补挂，
 * 但 URL / localStorage / media 三类来源不依赖 DOM，可立刻得出结果用于首屏 */
if (typeof window !== "undefined") apply();
