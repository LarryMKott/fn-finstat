<script setup>
/* 设置页：数据库、智能分类、备份恢复、运行日志、关于。
 * 改造点：原实现所有配置块平铺且每块都带长段说明，页面很长；
 * 现在把长说明收进可展开的「说明」区，默认只留一句摘要，降低视觉噪音 */
import { onMounted, reactive, ref, watch, computed } from "vue";
import { api, apiUrl } from "../api";
import { store } from "../store";
import { toast } from "../toast";
import { followingFnos, isDark, setTheme, themeMode } from "../theme";
import { sdkHosted } from "../fnos";
import AppIcon from "./AppIcon.vue";

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

const TYPE_LABEL = { sqlite: "SQLite（本地文件）", mysql: "MySQL", postgresql: "PostgreSQL" };
const DEFAULT_PORT = { mysql: 3306, postgresql: 5432 };

/* 可展开的说明块集合 */
const openNotes = ref({});
function toggleNote(key) {
  openNotes.value[key] = !openNotes.value[key];
}

const info = ref(null);
const form = reactive({
  db_type: "mysql",
  host: "127.0.0.1",
  port: DEFAULT_PORT.mysql,
  name: "",
  user: "root",
  password: "",
});
const testing = ref(false);
const migrating = ref(false);
const testResult = ref(null);
const migrateResult = ref(null);

async function loadInfo() {
  try {
    info.value = await api("/api/settings/database");
  } catch (err) {
    toast(err.message, true);
  }
}
onMounted(loadInfo);

/* ---- 关于（应用信息与作者） ---- */
const about = ref(null);

async function loadAbout() {
  try {
    about.value = await api("/api/settings/about");
  } catch (err) {
    /* 加载失败不打扰用户，关于块仅缺省 */
  }
}
onMounted(loadAbout);

const claiming = ref(false);

async function claimLegacy() {
  claiming.value = true;
  try {
    const res = await api("/api/settings/user/claim", { method: "POST" });
    toast(res.claimed > 0 ? `已认领 ${res.claimed} 条历史流水` : "没有需要认领的历史流水");
    await loadInfo();
  } catch (err) {
    toast(err.message, true);
  } finally {
    claiming.value = false;
  }
}

function accountLabel() {
  if (!info.value) return "";
  if (!info.value.user_id) return "本地模式（无网关身份）";
  return info.value.user_name ? `${info.value.user_name}（UID ${info.value.user_id}）` : `UID ${info.value.user_id}`;
}

function onTypeChange() {
  form.port = DEFAULT_PORT[form.db_type];
  testResult.value = null;
}

function targetPayload() {
  return {
    db_type: form.db_type,
    host: form.host.trim(),
    port: form.port || null,
    name: form.name.trim(),
    user: form.user.trim(),
    password: form.password,
  };
}

function validate() {
  if (!form.host.trim()) {
    toast("请填写主机地址", true);
    return false;
  }
  if (!form.name.trim()) {
    toast("请填写数据库名", true);
    return false;
  }
  return true;
}

async function testConnection() {
  if (!validate()) return;
  testing.value = true;
  testResult.value = null;
  try {
    testResult.value = await api("/api/settings/database/test", {
      method: "POST",
      body: JSON.stringify(targetPayload()),
    });
  } catch (err) {
    testResult.value = { ok: false, message: err.message };
  } finally {
    testing.value = false;
  }
}

async function migrate() {
  if (!validate()) return;
  const target = `${form.db_type.toUpperCase()}（${form.host}:${form.port}/${form.name}）`;
  const msg =
    `确定把当前数据库的 ${info.value?.bills ?? 0} 条流水、${info.value?.categories ?? 0} 个分类` +
    `迁移到 ${target} 吗？\n\n` +
    "迁移成功后将立即切换到新数据库；原数据库数据保留不动，可随时改回。";
  if (!confirm(msg)) return;
  migrating.value = true;
  migrateResult.value = null;
  try {
    migrateResult.value = await api("/api/settings/database/migrate", {
      method: "POST",
      body: JSON.stringify(targetPayload()),
    });
    toast("迁移完成，已切换到新数据库");
    await loadInfo();
  } catch (err) {
    migrateResult.value = { ok: false, message: err.message };
    toast(err.message, true);
  } finally {
    migrating.value = false;
  }
}

/* ---- 智能分类（DeepSeek）---- */
const AI_MODELS = [
  { value: "deepseek-chat", label: "deepseek-chat（V3，推荐）" },
  { value: "deepseek-reasoner", label: "deepseek-reasoner（R1，较慢较贵）" },
];
const ai = reactive({ enabled: false, api_key: "", base_url: "", model: "deepseek-chat" });
const aiInfo = ref(null);
const aiSaving = ref(false);
const aiTesting = ref(false);
const aiTestResult = ref(null);

async function loadAI() {
  try {
    aiInfo.value = await api("/api/ai/config");
    ai.enabled = aiInfo.value.enabled;
    ai.base_url = aiInfo.value.base_url;
    ai.model = aiInfo.value.model;
  } catch (err) {
    toast("智能分类配置加载失败：" + err.message, true);
  }
}

function aiPayload() {
  /* 密钥仅在表单里输入了新值时才提交（后端约定：不传 = 保持已保存的密钥） */
  const payload = { enabled: ai.enabled, base_url: ai.base_url, model: ai.model };
  if (ai.api_key.trim()) payload.api_key = ai.api_key.trim();
  return payload;
}

async function saveAI() {
  aiSaving.value = true;
  try {
    aiInfo.value = await api("/api/ai/config", {
      method: "PUT",
      body: JSON.stringify(aiPayload()),
    });
    ai.api_key = "";
    toast("智能分类配置已保存");
  } catch (err) {
    toast(err.message, true);
  } finally {
    aiSaving.value = false;
  }
}

async function testAI() {
  aiTesting.value = true;
  aiTestResult.value = null;
  try {
    aiTestResult.value = await api("/api/ai/test", {
      method: "POST",
      body: JSON.stringify(aiPayload()),
    });
  } catch (err) {
    aiTestResult.value = { ok: false, message: err.message };
  } finally {
    aiTesting.value = false;
  }
}

onMounted(loadAI);

/* ---- 备份与恢复 ---- */
const restoreFile = ref(null);
const restoreReplace = ref(false);
const restoring = ref(false);
const restoreResult = ref(null);

function onRestoreFile(e) {
  restoreFile.value = e.target.files[0] || null;
  restoreResult.value = null;
}

async function doRestore() {
  if (!restoreFile.value) {
    toast("请先选择备份 JSON 文件", true);
    return;
  }
  const msg = restoreReplace.value
    ? "覆盖模式将先清空当前数据库的全部数据再导入备份！\n确定继续吗？此操作不可恢复。"
    : "合并模式将按交易单号/分类名去重导入备份中的数据。\n确定继续吗？";
  if (!confirm(msg)) return;
  restoring.value = true;
  restoreResult.value = null;
  try {
    const fd = new FormData();
    fd.append("file", restoreFile.value);
    fd.append("replace", restoreReplace.value ? "true" : "false");
    restoreResult.value = await api("/api/settings/restore", { method: "POST", body: fd });
    toast("恢复完成");
    await loadInfo();
  } catch (err) {
    restoreResult.value = { ok: false, message: err.message };
    toast(err.message, true);
  } finally {
    restoring.value = false;
  }
}

/* ---- 运行日志 ---- */
const logLines = ref(300);
const logInfo = ref(null);
const logLoading = ref(false);
const logLoaded = ref(false);

async function loadLogs() {
  logLoading.value = true;
  try {
    logInfo.value = await api(`/api/settings/logs?lines=${logLines.value}`);
    logLoaded.value = true;
  } catch (err) {
    toast("运行日志加载失败：" + err.message, true);
  } finally {
    logLoading.value = false;
  }
}

function fmtSize(n) {
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MB";
  if (n >= 1024) return (n / 1024).toFixed(1) + " KB";
  return n + " B";
}

/* 面板常驻挂载，首次切到设置页时才加载日志，避免应用启动就拉取 */
watch(
  () => store.tab === "settings",
  (active) => {
    if (active && !logLoaded.value) loadLogs();
  },
  { immediate: true },
);
</script>

<template>
  <section class="panel" :class="{ active: store.tab === 'settings' }">
    <!-- 当前数据库 -->
    <div class="settings-box">
      <div class="section-head">
        <h3>当前数据库</h3>
        <span class="section-head__hint">数据隔离按飞牛登录账号生效，分类为全账号共享</span>
      </div>
      <template v-if="info">
        <ul class="db-meta">
          <li>
            <span class="k">当前账号</span>
            <span class="v"><span class="db-type-badge">{{ accountLabel() }}</span></span>
          </li>
          <li>
            <span class="k">数据库类型</span>
            <span class="v">{{ TYPE_LABEL[info.db_type] || info.db_type }}</span>
          </li>
          <li v-if="info.sqlite_path">
            <span class="k">数据文件</span>
            <span class="v">{{ info.sqlite_path }}</span>
          </li>
          <li v-else>
            <span class="k">连接地址</span>
            <span class="v">{{ info.host }}:{{ info.port }}</span>
          </li>
          <li v-if="!info.sqlite_path">
            <span class="k">数据库名</span>
            <span class="v">{{ info.name }}</span>
          </li>
          <li v-if="!info.sqlite_path">
            <span class="k">用户名</span>
            <span class="v">{{ info.user }}</span>
          </li>
          <li>
            <span class="k">流水 / 分类</span>
            <span class="v">{{ info.bills }} 条 / {{ info.categories }} 个</span>
          </li>
          <li>
            <span class="k">表结构版本</span>
            <span class="v">v{{ info.schema_version }}（最新 v{{ info.schema_latest }}）</span>
          </li>
        </ul>
        <div v-if="info.user_id && info.unassigned_bills > 0" class="claim-box">
          <span>检测到 {{ info.unassigned_bills }} 条升级前的历史流水（无账号归属）。</span>
          <button class="btn primary" :disabled="claiming" @click="claimLegacy">
            {{ claiming ? "认领中…" : "认领到当前账号" }}
          </button>
        </div>
      </template>
      <p v-else class="hint">加载中…</p>
    </div>

    <!-- 智能分类 -->
    <div class="settings-box">
      <div class="section-head">
        <h3>智能分类（DeepSeek）</h3>
        <button class="note-toggle" :aria-expanded="!!openNotes.ai" @click="toggleNote('ai')">
          {{ openNotes.ai ? "收起说明" : "配置说明" }}
        </button>
      </div>
      <p class="hint">
        配置 API Key 后，关键词未命中的流水会交给大模型语义归类；调用会产生少量 API 费用。
      </p>
      <div v-show="openNotes.ai" class="note-box">
        在
        <a href="https://platform.deepseek.com" target="_blank" rel="noopener">platform.deepseek.com</a>
        创建 API Key 后填入下方并测试连接。开启「导入时自动智能分类」后，导入账单先走内置关键词归类，
        仍未命中的记录自动交给 DeepSeek；也可在「流水」页点击「AI 智能分类」批量重归类存量流水（单次最多 1000 条）。
        AI 只能返回当前分类表中已有的名称，返回编造分类会被丢弃并保留原分类。
      </div>
      <div class="form-grid">
        <label class="field">
          API Key
          <input
            v-model="ai.api_key"
            type="password"
            :placeholder="aiInfo?.has_api_key ? `已设置（${aiInfo.api_key_hint}），留空保持不变` : 'sk-...'"
            autocomplete="new-password"
          />
        </label>
        <label class="field">
          模型
          <select v-model="ai.model">
            <option v-for="m in AI_MODELS" :key="m.value" :value="m.value">{{ m.label }}</option>
          </select>
        </label>
        <label class="field">
          API 地址
          <input v-model="ai.base_url" type="text" placeholder="https://api.deepseek.com" />
        </label>
        <div class="field">
          <span class="switch-grid-label">导入时自动智能分类</span>
          <label class="switch-row">
            <input v-model="ai.enabled" type="checkbox" />
            <em>{{ ai.enabled ? "已开启：仅对关键词未命中的「其他」流水调用" : "已关闭：仅使用内置关键词归类" }}</em>
          </label>
        </div>
      </div>
      <div class="settings-actions">
        <button class="btn" :disabled="aiTesting || aiSaving" @click="testAI">
          {{ aiTesting ? "测试中…" : "测试连接" }}
        </button>
        <button class="btn primary" :disabled="aiTesting || aiSaving" @click="saveAI">
          {{ aiSaving ? "保存中…" : "保存配置" }}
        </button>
      </div>
      <div class="settings-result" :class="{ show: !!aiTestResult, ok: aiTestResult?.ok, err: aiTestResult && !aiTestResult.ok }">
        <template v-if="aiTestResult">
          <template v-if="aiTestResult.ok"><b>连接成功</b>，可以开始智能分类</template>
          <template v-else>连接失败：{{ aiTestResult.message }}</template>
        </template>
      </div>
    </div>

    <!-- 迁移数据库 -->
    <div class="settings-box">
      <div class="section-head">
        <h3>迁移到新数据库</h3>
        <button class="note-toggle" :aria-expanded="!!openNotes.migrate" @click="toggleNote('migrate')">
          {{ openNotes.migrate ? "收起说明" : "迁移说明" }}
        </button>
      </div>
      <p class="hint">把全部流水与分类搬到新的 MySQL / PostgreSQL，并立即切换，无需重启应用。</p>
      <div v-show="openNotes.migrate" class="note-box">
        迁移成功后立即切换到新数据库；原数据库只读保留不动，改回配置即可回退。
        目标库为空时整库搬移并保留流水编号；目标库已有数据时按交易单号去重合并。
        目标库驱动缺失时会自动安装，无需重装应用。
      </div>
      <div class="form-grid">
        <label class="field">
          数据库类型
          <select v-model="form.db_type" @change="onTypeChange">
            <option value="mysql">MySQL</option>
            <option value="postgresql">PostgreSQL</option>
          </select>
        </label>
        <label class="field">
          主机地址
          <input v-model="form.host" type="text" placeholder="如 192.168.1.10" />
        </label>
        <label class="field">
          端口
          <input v-model.number="form.port" type="number" :placeholder="String(DEFAULT_PORT[form.db_type])" />
        </label>
        <label class="field">
          数据库名
          <input v-model="form.name" type="text" placeholder="如 fn_finstat" />
        </label>
        <label class="field">
          用户名
          <input v-model="form.user" type="text" placeholder="root" />
        </label>
        <label class="field">
          密码
          <input v-model="form.password" type="password" placeholder="数据库密码" />
        </label>
      </div>
      <div class="settings-actions">
        <button class="btn" :disabled="testing || migrating" @click="testConnection">
          {{ testing ? "测试中…" : "测试连接" }}
        </button>
        <button class="btn primary" :disabled="testing || migrating" @click="migrate">
          {{ migrating ? "迁移中，请稍候…" : "迁移并切换" }}
        </button>
      </div>

      <div class="settings-result" :class="{ show: !!testResult, ok: testResult?.ok, err: testResult && !testResult.ok }">
        <template v-if="testResult">
          <template v-if="testResult.ok">
            <b>连接成功</b>（{{ testResult.server_version }}）
            <span v-if="testResult.target_empty === true">· 目标库为空，将全量搬移并保留流水编号</span>
            <span v-else-if="testResult.target_empty === false">· 目标库已有数据，将按交易单号去重合并</span>
            <span v-else>· 目标库尚无业务表，迁移时自动创建</span>
          </template>
          <template v-else>连接失败：{{ testResult.message }}</template>
        </template>
      </div>

      <div class="settings-result" :class="{ show: !!migrateResult, ok: migrateResult?.ok, err: migrateResult && !migrateResult.ok }">
        <template v-if="migrateResult">
          <template v-if="migrateResult.ok">
            <b>迁移完成</b>：复制 {{ migrateResult.copied_bills }} 条流水、
            {{ migrateResult.copied_categories }} 个分类
            <span v-if="migrateResult.merged">（目标原有数据，已去重合并）</span>，
            已切换到新数据库。原数据库数据保留于原处，可随时改回。
          </template>
          <template v-else>迁移失败：{{ migrateResult.message }}</template>
        </template>
      </div>
    </div>

    <!-- 备份与恢复 -->
    <div class="settings-box">
      <div class="section-head">
        <h3>备份与恢复</h3>
        <button class="note-toggle" :aria-expanded="!!openNotes.backup" @click="toggleNote('backup')">
          {{ openNotes.backup ? "收起说明" : "模式说明" }}
        </button>
      </div>
      <p class="hint">
        备份导出全部账号的流水、分类、预算与资产快照为一个 JSON 文件，建议定期下载保存。
      </p>
      <div v-show="openNotes.backup" class="note-box">
        <b>合并模式</b>（默认）：按唯一键去重导入，适合把多份备份汇总到同一个库。
        无交易号的流水无法去重，重复恢复同一备份可能产生重复记录。
        <b>覆盖模式</b>：先清空全部业务表再导入，不可恢复，请先下载一份当前备份。
      </div>
      <div class="settings-actions">
        <a class="btn primary" :href="apiUrl('/api/settings/backup')">
          <AppIcon name="download" :size="15" /> 下载备份文件
        </a>
      </div>
      <div class="restore-row">
        <input type="file" accept=".json,application/json" aria-label="选择备份文件" @change="onRestoreFile" />
        <label class="switch-row">
          <input v-model="restoreReplace" type="checkbox" />
          覆盖模式<em>先清空全部现有数据，危险</em>
        </label>
        <button class="btn" :disabled="restoring" @click="doRestore">
          {{ restoring ? "恢复中…" : "从备份恢复" }}
        </button>
      </div>
      <div class="settings-result" :class="{ show: !!restoreResult, ok: restoreResult?.ok, err: restoreResult && !restoreResult.ok }">
        <template v-if="restoreResult && restoreResult.ok">
          恢复完成：流水 {{ restoreResult.bills }} 条 · 分类 {{ restoreResult.categories }} 个 ·
          预算 {{ restoreResult.budgets }} 条 · 资产快照 {{ restoreResult.assets }} 条
          <template v-if="restoreResult.skipped">（跳过格式异常 {{ restoreResult.skipped }} 行）</template>
        </template>
        <template v-else-if="restoreResult">恢复失败：{{ restoreResult.message }}</template>
      </div>
    </div>

    <!-- 运行日志 -->
    <div class="settings-box">
      <div class="section-head">
        <h3>运行日志</h3>
        <span class="section-head__hint">含账单导入与智能分类过程记录，单文件 10MB 自动轮转</span>
      </div>
      <div class="log-toolbar">
        <select v-model.number="logLines" :disabled="logLoading" aria-label="日志行数" @change="loadLogs">
          <option :value="100">最近 100 行</option>
          <option :value="300">最近 300 行</option>
          <option :value="1000">最近 1000 行</option>
        </select>
        <button class="btn" :disabled="logLoading" @click="loadLogs">
          {{ logLoading ? "加载中…" : "刷新" }}
        </button>
        <a class="btn" :href="apiUrl('/api/settings/logs/download')">
          <AppIcon name="download" :size="15" /> 下载完整日志
        </a>
        <span v-if="logInfo" class="log-meta" :title="logInfo.path">
          {{ logInfo.lines }} 行 · {{ fmtSize(logInfo.size) }} · {{ logInfo.path }}
        </span>
      </div>
      <pre v-if="logInfo" class="log-viewer">{{ logInfo.content || "（暂无日志）" }}</pre>
      <p v-if="logInfo && logInfo.truncated" class="hint">
        内容有截断，仅显示日志末尾部分，完整内容请下载查看。
      </p>
    </div>

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
    </div>
  </section>
</template>

<style scoped>
/* 长说明的折叠开关：默认收起，减少页面视觉长度 */
.note-toggle {
  padding: var(--space-1) var(--space-2-5);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-primary);
  font-size: var(--text-xs);
  cursor: pointer;
  transition: background var(--dur-fast) var(--ease-out);
}
.note-toggle:hover {
  background: var(--color-primary-soft);
}

.note-box {
  margin-bottom: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--color-surface-sunken);
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  line-height: var(--leading-relaxed);
}
.note-box b {
  color: var(--color-text);
}
.note-box a {
  color: var(--color-primary);
}

.restore-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin-top: var(--space-3);
}

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
</style>
