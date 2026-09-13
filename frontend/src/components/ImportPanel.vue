<script setup>
/* 账单导入：NAS 目录导入 + 四平台手动上传。
 * NAS 导入：指定 NAS 上的账单目录后浏览文件，来源（微信/支付宝/京东/云闪付）
 * 由后端按表头自动识别，无需选平台；支持一键导入全部已识别账单。
 * 手动上传：每个平台配一个缩写徽标做视觉识别，导入结果用结构化摘要。 */
import { computed, onMounted, reactive, ref } from "vue";
import { api } from "../api";
import { store } from "../store";
import { toast } from "../toast";
import AppIcon from "./AppIcon.vue";

const PLATFORMS = [
  {
    key: "wechat",
    title: "微信支付账单",
    abbr: "微",
    ext: "xlsx",
    hint: "微信 → 我 → 服务 → 钱包 → 账单 → 右上角「账单下载」→ 导出 Excel",
    accept: ".xlsx",
  },
  {
    key: "alipay",
    title: "支付宝账单",
    abbr: "支",
    ext: "csv",
    hint: "支付宝 → 我的 → 账单 → 交易流水证明 → 导出 CSV 流水",
    accept: ".csv",
  },
  {
    key: "jd",
    title: "京东金融账单",
    abbr: "京",
    ext: "csv",
    hint: "京东金融 App → 我的 → 账单 → 收支统计 → 导出 CSV 流水",
    accept: ".csv",
  },
  {
    key: "unionpay",
    title: "云闪付账单",
    abbr: "云",
    ext: "csv",
    hint: "云闪付 App → 首页「账单」→ 导出交易明细 CSV",
    accept: ".csv",
  },
];

/* NAS 来源徽标（key 与后端识别结果一致） */
const SOURCE_META = {
  wechat: { abbr: "微", label: "微信支付" },
  alipay: { abbr: "支", label: "支付宝" },
  jd: { abbr: "京", label: "京东金融" },
  unionpay: { abbr: "云", label: "云闪付" },
  unknown: { abbr: "?", label: "未识别" },
};

function sourceMeta(source) {
  return SOURCE_META[source] || SOURCE_META.unknown;
}

function formatSize(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(seconds) {
  if (!seconds) return "";
  return new Date(seconds * 1000).toLocaleDateString();
}

const files = ref(Object.fromEntries(PLATFORMS.map((p) => [p.key, null])));
const busy = ref(Object.fromEntries(PLATFORMS.map((p) => [p.key, false])));
const importResult = ref(null);
const inputs = ref({});

function onFileChange(kind, e) {
  files.value[kind] = e.target.files[0] || null;
}

async function upload(kind) {
  const file = files.value[kind];
  if (!file) {
    toast("请先选择文件", true);
    return;
  }
  importResult.value = null;
  busy.value[kind] = true;
  try {
    const fd = new FormData();
    fd.append("file", file);
    const res = await api("/api/upload/" + kind, { method: "POST", body: fd });
    importResult.value = { ok: true, ...res };
    toast(`导入完成：新增 ${res.inserted} 条`);
    const input = inputs.value[kind];
    if (input) input.value = "";
    files.value[kind] = null;
  } catch (err) {
    importResult.value = { ok: false, message: err.message };
  } finally {
    busy.value[kind] = false;
  }
}

/* ---- NAS 目录导入 ---- */
const nasConfig = ref(null); // /api/nas/config：{import_dir, exists, supported_exts}
const nasDirDraft = ref("");
const nasDir = ref(null); // /api/nas/files：目录浏览结果
const currentPath = ref("");
const nasSaving = ref(false);
const nasLoading = ref(false);
const nasImportingAll = ref(false);
const nasBusy = reactive({});

const identifiedCount = computed(() => {
  if (!nasDir.value) return 0;
  return nasDir.value.files.filter((f) => f.source !== "unknown").length;
});

onMounted(async () => {
  try {
    nasConfig.value = await api("/api/nas/config");
    nasDirDraft.value = nasConfig.value.import_dir || "";
    if (nasConfig.value.import_dir) await loadNasFiles("");
  } catch (err) {
    /* 目录配置加载失败不阻塞手动上传区 */
  }
});

async function saveNasConfig() {
  const dir = nasDirDraft.value.trim();
  if (!dir) {
    toast("请先填写 NAS 账单目录", true);
    return;
  }
  nasSaving.value = true;
  try {
    nasConfig.value = await api("/api/nas/config", {
      method: "PUT",
      body: JSON.stringify({ import_dir: dir }),
    });
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
    nasDir.value = await api(
      "/api/nas/files?path=" + encodeURIComponent(path || "")
    );
    currentPath.value = path || "";
  } catch (err) {
    toast(err.message, true);
  } finally {
    nasLoading.value = false;
  }
}

function showResult(result, label) {
  importResult.value = { ok: true, ...result, name: label };
}

async function importNas(file) {
  importResult.value = null;
  nasBusy[file.path] = true;
  try {
    const res = await api("/api/nas/import", {
      method: "POST",
      body: JSON.stringify({ path: file.path }),
    });
    showResult(res, file.name);
    toast(`导入完成：新增 ${res.inserted} 条`);
  } catch (err) {
    importResult.value = { ok: false, message: err.message };
  } finally {
    nasBusy[file.path] = false;
  }
}

async function importAllNas() {
  const targets = nasDir.value.files.filter((f) => f.source !== "unknown");
  if (!targets.length) return;
  nasImportingAll.value = true;
  importResult.value = null;
  const failed = [];
  let total = 0;
  let inserted = 0;
  let skipped = 0;
  let ai = 0;
  for (const file of targets) {
    nasBusy[file.path] = true;
    try {
      const res = await api("/api/nas/import", {
        method: "POST",
        body: JSON.stringify({ path: file.path }),
      });
      total += res.total;
      inserted += res.inserted;
      skipped += res.skipped;
      ai += res.ai_classified || 0;
    } catch (err) {
      failed.push(file.name);
    } finally {
      nasBusy[file.path] = false;
    }
  }
  nasImportingAll.value = false;
  if (inserted + skipped === 0 && failed.length) {
    importResult.value = { ok: false, message: failed.join("、") };
  } else {
    const label = failed.length
      ? `${targets.length - failed.length}/${targets.length} 个文件成功`
      : `${targets.length} 个文件全部导入`;
    showResult({ total, inserted, skipped, ai_classified: ai }, label);
    toast(
      failed.length
        ? `批量导入完成，${failed.length} 个文件失败`
        : `批量导入完成：新增 ${inserted} 条`
    );
  }
}
</script>

<template>
  <section class="panel" :class="{ active: store.tab === 'import' }">
    <!-- NAS 目录导入：自动识别来源 -->
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
                {{ sourceMeta(f.source).label }} · {{ formatSize(f.size) }}
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

    <div class="import-grid">
      <div v-for="p in PLATFORMS" :key="p.key" class="import-box">
        <div class="import-box__head">
          <span class="import-box__icon" aria-hidden="true">{{ p.abbr }}</span>
          <h3>{{ p.title }}</h3>
          <span class="badge">.{{ p.ext }}</span>
        </div>
        <p class="hint">{{ p.hint }}</p>
        <input
          :ref="(el) => (inputs[p.key] = el)"
          type="file"
          :accept="p.accept"
          :aria-label="`选择${p.title}文件`"
          @change="onFileChange(p.key, $event)"
        />
        <button class="btn primary" :disabled="busy[p.key]" @click="upload(p.key)">
          <AppIcon name="import" :size="15" />
          {{ busy[p.key] ? "导入中…" : "上传并导入" }}
        </button>
      </div>
    </div>

    <!-- 导入结果：结构化摘要，关键数字可快速扫读 -->
    <div class="import-result" :class="{ show: !!importResult }">
      <template v-if="importResult">
        <template v-if="importResult.ok">
          <div class="import-result__title ok">
            <AppIcon name="restore" :size="16" style="transform: rotate(180deg)" />
            导入完成<span v-if="importResult.name" class="import-result__name">
              · {{ importResult.name }}</span
            >
          </div>
          <dl class="import-stats">
            <div class="import-stat">
              <dt>解析</dt>
              <dd>{{ importResult.total }}</dd>
            </div>
            <div class="import-stat import-stat--ok">
              <dt>新增入库</dt>
              <dd>{{ importResult.inserted }}</dd>
            </div>
            <div class="import-stat">
              <dt>跳过重复</dt>
              <dd>{{ importResult.skipped }}</dd>
            </div>
            <div v-if="importResult.ai_classified" class="import-stat">
              <dt>AI 归类</dt>
              <dd>{{ importResult.ai_classified }}</dd>
            </div>
          </dl>
          <p class="hint import-result__note">
            重复账单按交易单号自动去重，可放心重复上传同一份账单
          </p>
        </template>
        <div v-else class="fail">导入失败：{{ importResult.message }}</div>
      </template>
    </div>
  </section>
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
.import-result__name {
  font-weight: 500;
  color: var(--color-text-secondary);
}
</style>
