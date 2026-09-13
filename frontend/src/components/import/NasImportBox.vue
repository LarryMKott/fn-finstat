<script setup>
/* NAS 目录导入：配置账单目录 → 浏览（来源自动识别）→ 单个/批量导入。
 * 导入结果通过 result 事件交由父组件（导入页统一结果区）展示。
 *
 * 飞牛环境 v0.4+：可在头部感知用户级目录授权状态并提供「申请授权」按钮；
 * 不可用 / 未启用 trim / scope 缺失一律降级为隐藏，保持原逻辑不受影响。
 */
import { computed, onMounted, reactive, ref } from "vue";
import {
  nasGetAuthorization,
  nasGetConfig,
  nasImport,
  nasListFiles,
  nasSaveConfig,
} from "../../api/upload";
import {
  authorizationStatus,
  pickUserDirectory,
  refreshAuthorizationStatus,
} from "../../fnosAuth";
import { fmtSize } from "../../utils/format";
import { sourceMeta } from "../../utils/constants";
import { toast } from "../../toast";
import AppIcon from "../AppIcon.vue";

const emit = defineEmits(["result"]);

const nasConfig = ref(null); // {import_dir, exists, supported_exts}
const nasDirDraft = ref("");
const nasDir = ref(null); // 目录浏览结果
const currentPath = ref("");
const nasSaving = ref(false);
const nasLoading = ref(false);
const nasImportingAll = ref(false);
const nasBusy = reactive({});
const authRequesting = ref(false);

const identifiedCount = computed(() => {
  if (!nasDir.value) return 0;
  return nasDir.value.files.filter((f) => f.source !== "unknown").length;
});

/** 飞牛授权区是否需要显示：「available=true 才显示」，本地/trim 不可用直接隐藏 */
const showAuthSection = computed(() => {
  return authorizationStatus.value?.available === true;
});
const authAuthorized = computed(
  () => showAuthSection.value && (authorizationStatus.value?.authorized || false),
);
const authFolders = computed(() =>
  Array.isArray(authorizationStatus.value?.folders)
    ? authorizationStatus.value.folders
    : [],
);
const authReason = computed(
  () => authorizationStatus.value?.reason || "",
);

onMounted(async () => {
  // 并行拉取配置与授权状态；任一失败不影响另一条
  try {
    const [cfg] = await Promise.all([
      nasGetConfig(),
      refreshAuthorizationStatus().catch(() => null),
    ]);
    nasConfig.value = cfg;
    nasDirDraft.value = cfg.import_dir || "";
    if (cfg.import_dir) await loadNasFiles("");
  } catch {
    /* 目录配置加载失败不阻塞手动上传区 */
  }
});

async function requestAuthorization() {
  if (authRequesting.value) return;
  authRequesting.value = true;
  try {
    const res = await pickUserDirectory();
    if (res.success) {
      toast(`授权成功：已添加 ${res.paths.length} 个目录`);
    } else {
      toast(res.reason || "授权请求未完成", true);
    }
  } catch (err) {
    toast(err?.message || "授权请求异常", true);
  } finally {
    authRequesting.value = false;
  }
}

async function refreshAuthorization() {
  await refreshAuthorizationStatus();
  if (authorizationStatus.value?.available) {
    toast(
      authorizationStatus.value.authorized
        ? `已授权 ${authorizationStatus.value.folders.length} 个目录`
        : "尚未授权任何目录",
    );
  }
}

async function saveNasConfig() {
  const dir = nasDirDraft.value.trim();
  if (!dir) {
    toast("请先填写 NAS 账单目录", true);
    return;
  }
  nasSaving.value = true;
  try {
    nasConfig.value = await nasSaveConfig(dir);
    nasDirDraft.value = nasConfig.value.import_dir;
    toast("账单目录已保存");
    await loadNasFiles("");
  } catch (err) {
    toast(err.message, true);
  } finally {
    nasSaving.value = false;
  }
}

async function loadNasFiles(path) {
  nasLoading.value = true;
  try {
    nasDir.value = await nasListFiles(path || "");
    currentPath.value = path || "";
  } catch (err) {
    toast(err.message, true);
  } finally {
    nasLoading.value = false;
  }
}

function formatDate(seconds) {
  if (!seconds) return "";
  return new Date(seconds * 1000).toLocaleDateString();
}

async function importNas(file) {
  emit("result", null);
  nasBusy[file.path] = true;
  try {
    const res = await nasImport(file.path);
    emit("result", { ok: true, ...res, name: file.name });
    toast(`导入完成：新增 ${res.inserted} 条`);
  } catch (err) {
    emit("result", { ok: false, message: err.message });
  } finally {
    nasBusy[file.path] = false;
  }
}

async function importAllNas() {
  const targets = nasDir.value.files.filter((f) => f.source !== "unknown");
  if (!targets.length) return;
  nasImportingAll.value = true;
  emit("result", null);
  const failed = [];
  let total = 0;
  let inserted = 0;
  let skipped = 0;
  let ai = 0;
  for (const file of targets) {
    nasBusy[file.path] = true;
    try {
      const res = await nasImport(file.path);
      total += res.total;
      inserted += res.inserted;
      skipped += res.skipped;
      ai += res.ai_classified || 0;
    } catch {
      failed.push(file.name);
    } finally {
      nasBusy[file.path] = false;
    }
  }
  nasImportingAll.value = false;
  if (inserted + skipped === 0 && failed.length) {
    emit("result", { ok: false, message: failed.join("、") });
  } else {
    const label = failed.length
      ? `${targets.length - failed.length}/${targets.length} 个文件成功`
      : `${targets.length} 个文件全部导入`;
    emit("result", { ok: true, total, inserted, skipped, ai_classified: ai, name: label });
    toast(
      failed.length
        ? `批量导入完成，${failed.length} 个文件失败`
        : `批量导入完成：新增 ${inserted} 条`,
    );
  }
}
</script>

<template>
  <div class="import-box nas-box">
    <div class="import-box__head">
      <span class="import-box__icon" aria-hidden="true">NAS</span>
      <h3>NAS 目录导入</h3>
      <span class="badge">自动识别来源</span>
    </div>
    <p class="hint">
      把微信/支付宝/京东/云闪付导出的账单文件放进 NAS 的同一个目录，
      导入时按表头自动识别来源，无需选择平台。支持
      {{ nasConfig?.supported_exts?.join(" / ") || ".csv / .xlsx" }} 文件。
    </p>

    <!-- 飞牛环境：用户已授权目录状态（不可用时不显示） -->
    <div v-if="showAuthSection" class="nas-auth">
      <div class="nas-auth__status">
        <AppIcon
          :name="authAuthorized ? 'check' : 'alert'"
          :size="14"
          class="nas-auth__icon"
          :class="authAuthorized ? 'ok' : 'warn'"
        />
        <span class="nas-auth__label">
          <template v-if="authAuthorized">
            已授权 {{ authFolders.length }} 个账单目录
          </template>
          <template v-else>尚未授权账单目录</template>
        </span>
        <span v-if="authReason && !authAuthorized" class="nas-auth__reason">
          {{ authReason }}
        </span>
      </div>
      <div class="nas-auth__actions">
        <button
          class="btn"
          :disabled="authRequesting"
          @click="requestAuthorization"
        >
          {{
            authRequesting
              ? "授权中…"
              : authAuthorized
                ? "修改授权目录"
                : "申请授权目录"
          }}
        </button>
        <button class="btn" @click="refreshAuthorization">刷新状态</button>
      </div>
      <ul v-if="authAuthorized" class="nas-auth__folders">
        <li
          v-for="(p, i) in authFolders"
          :key="i"
          class="nas-auth__folder"
          :title="p"
        >
          {{ p }}
        </li>
      </ul>
    </div>

    <div class="nas-config">
      <input
        v-model="nasDirDraft"
        type="text"
        placeholder="NAS 账单目录绝对路径，如 /vol1/1000/bills"
        aria-label="NAS 账单目录"
        @keyup.enter="saveNasConfig"
      />
      <button class="btn" :disabled="nasSaving" @click="saveNasConfig">
        {{ nasSaving ? "保存中…" : "保存目录" }}
      </button>
      <button
        class="btn"
        :disabled="nasLoading || !nasConfig?.import_dir"
        @click="loadNasFiles(currentPath)"
      >
        {{ nasLoading ? "加载中…" : "刷新" }}
      </button>
    </div>
    <p
      v-if="nasConfig && nasConfig.import_dir && !nasConfig.exists"
      class="hint nas-warn"
    >
      目录当前不存在或不可访问，请检查路径是否正确
    </p>

    <template v-if="nasDir">
      <div class="nas-pathbar">
        <button
          v-if="nasDir.parent"
          class="btn nas-up"
          @click="loadNasFiles(nasDir.parent)"
        >
          ← 上一级
        </button>
        <span class="nas-path" :title="nasDir.root">
          {{ nasDir.root }}<template v-if="nasDir.path">/{{ nasDir.path }}</template>
        </span>
      </div>

      <ul v-if="nasDir.dirs.length || nasDir.files.length" class="nas-list">
        <li
          v-for="d in nasDir.dirs"
          :key="d.path"
          class="nas-row nas-row--dir"
          role="button"
          tabindex="0"
          @click="loadNasFiles(d.path)"
          @keyup.enter="loadNasFiles(d.path)"
        >
          <span class="nas-src" aria-hidden="true">DIR</span>
          <div class="nas-file">
            <span class="nas-name">{{ d.name }}</span>
            <span class="nas-meta">子目录</span>
          </div>
        </li>
        <li v-for="f in nasDir.files" :key="f.path" class="nas-row">
          <span
            class="nas-src"
            :class="`nas-src--${f.source}`"
            :title="sourceMeta(f.source).label"
            aria-hidden="true"
          >
            {{ sourceMeta(f.source).abbr }}
          </span>
          <div class="nas-file">
            <span class="nas-name">{{ f.name }}</span>
            <span class="nas-meta">
              {{ sourceMeta(f.source).label }} · {{ fmtSize(f.size) }}
              <template v-if="f.modified"> · {{ formatDate(f.modified) }}</template>
            </span>
          </div>
          <button
            class="btn"
            :disabled="nasBusy[f.path] || nasImportingAll"
            @click="importNas(f)"
          >
            {{ nasBusy[f.path] ? "导入中…" : "导入" }}
          </button>
        </li>
      </ul>
      <p v-else class="hint nas-empty">
        目录里还没有账单文件，把导出的账单放进来后点「刷新」
      </p>

      <div v-if="identifiedCount > 0" class="nas-actions">
        <button
          class="btn primary"
          :disabled="nasImportingAll"
          @click="importAllNas"
        >
          <AppIcon name="import" :size="15" />
          {{
            nasImportingAll
              ? "批量导入中…"
              : `一键导入全部已识别（${identifiedCount} 个文件）`
          }}
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.nas-box .nas-config {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}
.nas-config input[type="text"] {
  flex: 1;
  min-width: 240px;
}
.nas-warn {
  color: var(--color-warn);
}

/* 飞牛用户授权区（available=false 时整段 v-if 不渲染） */
.nas-auth {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
  padding: var(--space-2-5);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-sunken);
}
.nas-auth__status {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  font-weight: 500;
}
.nas-auth__icon.ok {
  color: var(--color-success);
}
.nas-auth__icon.warn {
  color: var(--color-warn);
}
.nas-auth__reason {
  font-weight: 400;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}
.nas-auth__actions {
  display: flex;
  gap: var(--space-2);
}
.nas-auth__folders {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.nas-auth__folder {
  font-family: var(--font-numeric);
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
  background: var(--color-surface);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  direction: rtl; /* 长路径保留尾部目录名 */
  text-align: left;
}
.nas-pathbar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: var(--space-3) 0 var(--space-2);
}
.nas-up {
  flex-shrink: 0;
}
.nas-path {
  font-family: var(--font-numeric);
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  direction: rtl; /* 长路径省略头部，保留尾部目录名 */
}
.nas-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.nas-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface);
}
.nas-row:last-child {
  border-bottom: none;
}
.nas-row--dir {
  cursor: pointer;
  transition: background var(--dur-fast) var(--ease-out);
}
.nas-row--dir:hover {
  background: var(--color-surface-sunken);
}
.nas-src {
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  border-radius: var(--radius-md);
  background: var(--color-surface-sunken);
  color: var(--color-text-tertiary);
  font-size: var(--text-xs);
  font-weight: 700;
}
/* 平台品牌色：少数允许的硬编码特例（品牌识别优先于令牌统一） */
.nas-src--wechat {
  background: rgba(7, 193, 96, 0.13);
  color: #17914f;
}
.nas-src--alipay {
  background: rgba(22, 119, 255, 0.12);
  color: #2b7fff;
}
.nas-src--jd {
  background: rgba(228, 54, 43, 0.11);
  color: #e04a3f;
}
.nas-src--unionpay {
  background: rgba(216, 145, 32, 0.15);
  color: #c07f16;
}
.nas-file {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.nas-name {
  font-size: var(--text-sm);
  font-weight: 550;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.nas-meta {
  font-size: var(--text-2xs);
  color: var(--color-text-tertiary);
}
.nas-row .btn {
  flex-shrink: 0;
}
.nas-empty {
  padding: var(--space-3);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
  text-align: center;
}
.nas-actions {
  margin-top: var(--space-3);
}
</style>
