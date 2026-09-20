<script setup>
/* 设置-外观与关于：主题跟随状态反馈、模式切换、应用信息、版本更新检查
 * （版本更新并入「关于」卡片：用户看到版本号的地方，正是会想起
 *  「是不是该升级了」的地方，拆成独立卡片反而要多跳一次视线） */
import { computed, onMounted, ref } from "vue";
import {
  ACCENTS,
  followingFnos,
  isDark,
  setAccent,
  setTheme,
  themeAccent,
  themeMode,
} from "../../theme";
import { sdkHosted } from "../../fnos";
import { aboutInfo } from "../../api/settings";
import { checkUpdate } from "../../api/update";
import { isoDate } from "../../utils/datetime";
import { isBusy, runTask } from "../../composables/useLoading";
import AppIcon from "../AppIcon.vue";

/* ---- 外观：主题模式与跟随状态 ---- */
const THEME_OPTIONS = [
  { value: "auto", label: "跟随系统", icon: "sparkles" },
  { value: "light", label: "日间", icon: "sun" },
  { value: "dark", label: "夜间", icon: "moon" },
];

/* 状态文案：明确告诉用户当前是「跟上了飞牛」还是「退回系统偏好」 */
const appearanceLabel = computed(() => {
  const scheme = isDark.value ? "夜间模式" : "日间模式";
  return followingFnos.value ? `飞牛 · ${scheme}` : `${scheme}`;
});

/* 通道说明：官方 SDK 可用时优先说明，让用户知道走的是系统推荐通道 */
const appearanceChannel = computed(() => {
  if (sdkHosted.value) return "已通过飞牛官方 SDK 实时接收主题变化，切换即时生效。";
  if (followingFnos.value) return "已自动跟随飞牛的日间 / 夜间设置，切换后界面配色实时同步。";
  return "";
});

/* ---- 关于（应用信息与作者） ---- */
const about = ref(null);

/* ---- 版本更新 ----
 * 只做「检查 + 引导下载」：飞牛第三方应用的安装与升级由系统应用中心完成，
 * 应用无权替换自身安装目录，因此这里不提供（也无法提供）「一键升级」。 */
const UPDATE_KEY = "update:check";
const updateResult = ref(null);
const showNotes = ref(false);

/**
 * 检查更新。
 * @param {object}  options
 * @param {boolean} options.manual true=用户主动点击：展示进度浮层并绕过后端结果缓存；
 *                                 false=进页面自动检查：静默执行，不闪浮层
 */
function runCheck({ manual = false } = {}) {
  return runTask({
    key: UPDATE_KEY,
    title: "检查更新",
    detail: "正在查询发布版本…",
    silent: !manual,
    mode: manual ? "queue" : "latest",
    successText: (result) => (result && result.message) || "检查完成",
    /* 离线 / 接口异常是「查不到」而非「查到了、一切正常」：走错误相位，
     * 免得浮层弹绿勾却写着「无法连接更新服务器」 */
    failed: (result) => !result || result.ok === false,
    rethrow: false,
    task: async () => {
      const result = await checkUpdate(manual);
      updateResult.value = result;
      showNotes.value = false; // 换了结果就收起上一份说明，避免旧内容配新版本号
      return result;
    },
  });
}

const recheck = () => runCheck({ manual: true });

const channelLabel = computed(() =>
  updateResult.value?.channel === "dev" ? "测试版渠道" : "正式版渠道",
);

/* 结论行配色：有新版本用主色（需要行动）、其余用中性色（无需操作，不抢注意力） */
const updateTone = computed(() => {
  const result = updateResult.value;
  if (!result) return "";
  if (!result.ok) return "is-error";
  return result.relation === "newer" ? "is-newer" : "is-same";
});

const updateIcon = computed(() => {
  const result = updateResult.value;
  if (!result || !result.ok) return "alert";
  return result.relation === "newer" ? "sparkles" : "check";
});

const hasUpdate = computed(
  () => !!updateResult.value?.ok && updateResult.value.relation === "newer",
);

onMounted(() => {
  /* 关于信息是轻量请求，失败静默即可，但同样走统一通道保证不会残留 loading */
  runTask({
    key: "about:load",
    title: "读取应用信息",
    rethrow: false,
    task: async () => {
      about.value = await aboutInfo();
    },
  });
  /* 进设置页自动静默检查一次：用户不必先点一下才知道有没有新版。
   * 后端有 5 分钟结果缓存，反复进出设置页不会反复请求发布接口。 */
  runCheck();
});
</script>

<template>
  <!-- 外观：主题跟随状态。飞牛宿主主题为自动探测，此处仅做可见性反馈 -->
  <div class="settings-box appearance-box">
    <div class="section-head">
      <h3>外观</h3>
      <span class="theme-status" :class="followingFnos ? 'is-fnos' : 'is-system'">
        <AppIcon :name="isDark ? 'moon' : 'sun'" :size="14" />
        {{ appearanceLabel }}
      </span>
    </div>
    <p class="hint appearance-hint">
      {{
        appearanceChannel
          || (themeMode === "auto"
            ? "未检测到飞牛主题设置，当前跟随浏览器 / 系统的深浅色偏好。"
            : "当前为手动指定主题，不再自动跟随；切回「跟随系统」即可恢复自动同步。")
      }}
    </p>
    <div class="theme-picker" role="group" aria-label="主题模式">
      <button
        v-for="opt in THEME_OPTIONS"
        :key="opt.value"
        type="button"
        class="theme-chip"
        :class="{ 'is-active': themeMode === opt.value }"
        :aria-pressed="themeMode === opt.value"
        @click="setTheme(opt.value)"
      >
        <AppIcon :name="opt.icon" :size="15" />
        {{ opt.label }}
      </button>
    </div>

    <!-- 配色主题：与日间/夜间独立，每个配色都自带日间+夜间两套 -->
    <p class="hint palette-label">配色（每个配色都适配日间与夜间）</p>
    <div class="theme-picker palette-picker" role="group" aria-label="配色主题">
      <button
        v-for="a in ACCENTS"
        :key="a.value"
        type="button"
        class="theme-chip palette-chip"
        :class="{ 'is-active': themeAccent === a.value }"
        :aria-pressed="themeAccent === a.value"
        @click="setAccent(a.value)"
      >
        <span class="palette-dot" :style="{ background: a.swatch }" aria-hidden="true"></span>
        {{ a.label }}
      </button>
    </div>
    <p class="hint">
      图标与界面配色均提供日间 / 夜间两套：日间版在浅色背景下醒目，夜间版在深色背景下柔和护眼。
    </p>
  </div>

  <!-- 关于 -->
  <div class="settings-box about-box">
    <div class="section-head">
      <h3>关于</h3>
    </div>
    <template v-if="about">
      <p class="about-line">
        <b>{{ about.app_name }}</b>
        <span class="about-version">v{{ about.version }}</span>
      </p>
      <p class="about-line muted">{{ about.description }}</p>
      <p class="about-line">
        作者：
        <a :href="about.author_url" target="_blank" rel="noopener">{{ about.author }}</a>
      </p>
      <p class="about-line">
        项目地址：
        <a :href="about.repo_url" target="_blank" rel="noopener">{{ about.repo_url }}</a>
      </p>
      <p class="about-line muted">开源协议：MIT</p>
    </template>

    <!-- 版本更新 -->
    <div class="update-box">
      <div class="update-head">
        <span class="update-title">版本更新</span>
        <button
          type="button"
          class="btn mini"
          :disabled="isBusy(UPDATE_KEY)"
          @click="recheck"
        >
          <AppIcon name="refresh" :size="14" />
          {{ updateResult ? "重新检查" : "检查更新" }}
        </button>
      </div>

      <p v-if="!updateResult" class="hint">正在检查是否有新版本…</p>

      <template v-else>
        <p class="update-line" :class="updateTone">
          <AppIcon :name="updateIcon" :size="15" />
          <span>{{ updateResult.message }}</span>
        </p>

        <p v-if="updateResult.ok" class="hint update-meta">
          {{ channelLabel }} · 本机 v{{ updateResult.current_version }}
          <template v-if="updateResult.checked_at"> · 检查于 {{ updateResult.checked_at }}</template>
          <template v-if="updateResult.cached"> · 5 分钟内已查过，点击「重新检查」可再拉一次</template>
        </p>
        <p v-else class="hint update-meta">
          检查更新需要能访问发布站点，离线部署不影响本应用使用；网络恢复后重新检查即可。
        </p>

        <template v-if="hasUpdate">
          <p class="hint update-meta">
            {{ updateResult.release_name }}
            <template v-if="updateResult.published_at">
              · 发布于 {{ isoDate(updateResult.published_at) }}
            </template>
          </p>

          <div v-if="updateResult.notes" class="update-notes-wrap">
            <button type="button" class="note-toggle" @click="showNotes = !showNotes">
              {{ showNotes ? "收起更新说明" : "查看更新说明" }}
            </button>
            <div v-if="showNotes" class="update-notes">
              <pre>{{ updateResult.notes }}</pre>
            </div>
          </div>

          <div class="update-actions">
            <a
              v-if="updateResult.download_url"
              class="btn primary mini"
              :href="updateResult.download_url"
              target="_blank"
              rel="noopener"
            >
              <AppIcon name="download" :size="14" />
              下载 v{{ updateResult.latest_version }}
            </a>
            <a
              v-if="updateResult.page_url"
              class="btn mini"
              :href="updateResult.page_url"
              target="_blank"
              rel="noopener"
            >
              查看发布详情
            </a>
          </div>

          <p class="hint">
            下载后在飞牛应用中心手动安装即完成升级，数据与配置保留不变。
            <template v-if="updateResult.checksum_url">
              可用
              <a :href="updateResult.checksum_url" target="_blank" rel="noopener">MD5 校验文件</a>
              核对下载是否完整。
            </template>
          </p>
        </template>
      </template>
    </div>
  </div>
</template>

<style scoped>
/* ---- 外观：主题跟随状态 ---- */
.theme-status {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1-5);
  padding: var(--space-1) var(--space-2-5);
  border-radius: var(--radius-pill);
  font-size: var(--text-xs);
  font-weight: 600;
  white-space: nowrap;
}
/* 已跟上飞牛：用主色系（积极状态）；退回系统偏好：中性色（不喧宾夺主） */
.theme-status.is-fnos {
  background: var(--color-primary-soft);
  color: var(--color-primary);
}
.theme-status.is-system {
  background: var(--color-surface-sunken);
  color: var(--color-text-secondary);
}

.appearance-hint {
  margin-top: var(--space-1);
}

.theme-picker {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin: var(--space-3) 0 var(--space-2);
}
.theme-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1-5);
  min-height: 34px;
  padding: 0 var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-pill);
  background: var(--color-surface);
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  cursor: pointer;
  transition:
    background var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out);
}
.theme-chip:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}
.theme-chip.is-active {
  border-color: var(--color-primary);
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-weight: 600;
}
.theme-chip:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: 2px;
}

/* 配色选择：圆点展示各主题主色（静态色板参考，非当前令牌） */
.palette-label {
  margin-top: var(--space-2);
}
.palette-dot {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  flex: none;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.35);
}

/* ---- 版本更新 ---- */
.update-box {
  margin-top: var(--space-4);
  padding-top: var(--space-4);
  border-top: 1px solid var(--color-divider);
}
.update-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}
.update-title {
  font-size: var(--text-sm);
  font-weight: 600;
}
.update-line {
  display: flex;
  align-items: center;
  gap: var(--space-1-5);
  margin-top: var(--space-3);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
}
.update-line.is-newer {
  color: var(--color-primary);
  font-weight: 600;
}
.update-line.is-same {
  color: var(--color-text-secondary);
}
.update-line.is-error {
  color: var(--color-warn);
}
.update-meta {
  margin-top: var(--space-1);
}

.update-notes-wrap {
  margin-top: var(--space-2);
}
.update-notes {
  margin-top: var(--space-2);
  padding: var(--space-3);
  max-height: 240px;
  overflow: auto;
  border-radius: var(--radius-md);
  background: var(--color-surface-sunken);
}
.update-notes pre {
  margin: 0;
  /* 说明是纯文本 Markdown，用正文字体而非等宽字体：中文正文可读性优先 */
  font-family: inherit;
  font-size: var(--text-xs);
  line-height: var(--leading-relaxed);
  color: var(--color-text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}

.update-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin: var(--space-3) 0 var(--space-2);
}
</style>
