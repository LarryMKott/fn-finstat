<script setup>
/* AI 消费分析报告弹窗：
 * - 支持月/季/半年/年四种周期（period_type + period_value 双输入）
 * - 两步流：先生成预览（消耗 DeepSeek 配额），再点归档保存（按周期唯一键覆盖）
 * - 查看归档不消耗配额；归档列表支持查看/删除
 * 复用既有极简 Markdown 渲染（标题/列表/粗体/分隔线/代码），不引入渲染库。 */
import { computed, ref, watch } from "vue";
import {
  aiArchiveReport,
  aiDeleteArchived,
  aiGenerateReport,
  aiGetArchived,
  aiListArchived,
} from "../api/ai";
import { pad2, previousMonth } from "../utils/datetime";
import { isBusy, runTask } from "../composables/useLoading";
import AppIcon from "./AppIcon.vue";

const props = defineProps({ show: Boolean });
const emit = defineEmits(["close"]);

/* 周期类型与可选项：与后端 PERIOD_TYPES 严格对齐 */
const PERIOD_TYPES = [
  { value: "month", label: "月度" },
  { value: "quarter", label: "季度" },
  { value: "half", label: "半年度" },
  { value: "year", label: "年度" },
];
const QUARTERS = [1, 2, 3, 4];
const HALVES = [1, 2];

const periodType = ref("month");
/* 各类型独立的值输入：切换类型时互不影响，避免丢失已选 */
const monthValue = ref(previousMonth());
const quarterYear = ref(new Date().getFullYear());
const quarter = ref(lastQuarterOf(new Date()));
const halfYear = ref(new Date().getFullYear());
const half = ref(lastHalfOf(new Date()));
const yearValue = ref(new Date().getFullYear() - 1);

function lastQuarterOf(now) {
  const q = Math.floor((now.getMonth() - 1) / 3 + 1);
  return q === 1 ? 4 : q - 1; // 1 月时上一季度是去年 Q4，但年份由外层处理；这里只返回季度序号
}
function lastHalfOf(now) {
  return now.getMonth() <= 5 ? 2 : 1; // 上半年 → 上一年 H2，下半年 → 当年 H1
}

/* 当前周期的合法 period_value 字符串 */
const periodValue = computed(() => {
  const t = periodType.value;
  if (t === "month") return monthValue.value;
  if (t === "quarter")
    return `${quarterYear.value}-Q${quarter.value}`;
  if (t === "half") return `${halfYear.value}-H${half.value}`;
  return `${yearValue.value}`;
});

/* 当前预览：生成或查看归档后填入；包含归档所需的全部字段 */
const preview = ref(null);
/* preview 来源：'generate' 表示新生成可归档，'archive' 表示查看归档不可重复归档 */
const previewSource = ref(null);
const archived = ref([]);
const archivedLoaded = ref(false);

const busy = computed(() => isBusy("ai:report:generate") || isBusy("ai:report:archive"));
const canArchive = computed(
  () => preview.value && previewSource.value === "generate"
);

/* 弹窗显示时加载归档列表（关闭再打开保证最新） */
watch(
  () => props.show,
  (visible) => {
    if (visible) loadArchived();
  }
);

async function loadArchived() {
  try {
    archived.value = await aiListArchived();
  } catch (err) {
    /* 列表失败不阻塞生成功能 */
    archived.value = [];
  } finally {
    archivedLoaded.value = true;
  }
}

async function generate() {
  const res = await runTask({
    key: "ai:report:generate",
    title: "生成 AI 报告",
    detail: `正在分析 ${periodValue.value} 的收支数据…`,
    rethrow: false,
    successText: "报告已生成",
    task: () => aiGenerateReport(periodType.value, periodValue.value),
  });
  if (!res) return;
  preview.value = {
    period_type: res.period_type,
    period_value: res.period_value,
    title: res.title,
    report: res.report,
    context: res.context,
  };
  previewSource.value = "generate";
}

async function archive() {
  if (!preview.value) return;
  const res = await runTask({
    key: "ai:report:archive",
    title: "归档报告",
    detail: `正在保存 ${preview.value.period_value} 的报告…`,
    rethrow: false,
    successText: "已归档",
    task: () =>
      aiArchiveReport({
        periodType: preview.value.period_type,
        periodValue: preview.value.period_value,
        title: preview.value.title,
        content: preview.value.report,
        statsSummary: preview.value.context,
      }),
  });
  if (!res) return;
  /* 归档成功后切换为「查看归档」状态，避免重复归档 */
  previewSource.value = "archive";
  await loadArchived();
}

async function viewArchived(item) {
  try {
    const detail = await runTask({
      key: "ai:report:view",
      title: "查看归档报告",
      detail: `正在读取「${item.title}」…`,
      rethrow: false,
      task: () => aiGetArchived(item.id),
    });
    if (!detail) return;
    preview.value = {
      period_type: detail.period_type,
      period_value: detail.period_value,
      title: detail.title,
      report: detail.content,
      context: detail.stats_summary
        ? safeParseJson(detail.stats_summary)
        : null,
    };
    previewSource.value = "archive";
  } catch (err) {
    /* 错误已由 runTask toast */
  }
}

async function removeArchived(item) {
  if (!window.confirm(`确认删除「${item.title}」？此操作不可撤销。`)) return;
  try {
    await aiDeleteArchived(item.id);
    /* 如果删除的是当前预览来源，清空预览 */
    if (preview.value && preview.value.period_value === item.period_value) {
      preview.value = null;
      previewSource.value = null;
    }
    await loadArchived();
  } catch (err) {
    /* toast 由全局错误处理 */
  }
}

function safeParseJson(s) {
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}

/* 切换周期类型时，重置预览（避免不同周期的旧预览误导） */
watch(periodType, () => {
  preview.value = null;
  previewSource.value = null;
});

/* 极简 Markdown → HTML：只处理报告实际用到的语法，避免引入渲染库。
 * 先转义 HTML 再做替换，防止模型输出意外注入标签。 */
function escapeHtml(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
function inlineMd(s) {
  return s
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.+?)`/g, "<code>$1</code>");
}
const reportHtml = computed(() => {
  if (!preview.value) return "";
  const src = escapeHtml(preview.value.report || "").replace(/\r\n/g, "\n");
  const lines = src.split("\n");
  const out = [];
  let inList = false;
  const closeList = () => {
    if (inList) {
      out.push("</ul>");
      inList = false;
    }
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      closeList();
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      closeList();
      const level = Math.min(heading[1].length + 1, 5);
      out.push(`<h${level}>${inlineMd(heading[2])}</h${level}>`);
      continue;
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      if (!inList) {
        out.push("<ul>");
        inList = true;
      }
      out.push(`<li>${inlineMd(line.replace(/^\s*[-*+]\s+/, ""))}</li>`);
      continue;
    }
    if (/^\s*(---|\*\*\*)\s*$/.test(line)) {
      closeList();
      out.push("<hr />");
      continue;
    }
    closeList();
    out.push(`<p>${inlineMd(line)}</p>`);
  }
  closeList();
  return out.join("");
});

/* 归档列表按周期类型分组展示，便于查找 */
const groupedArchived = computed(() => {
  const groups = new Map();
  for (const item of archived.value) {
    if (!groups.has(item.period_type)) groups.set(item.period_type, []);
    groups.get(item.period_type).push(item);
  }
  return PERIOD_TYPES.map((t) => ({
    type: t.value,
    label: t.label,
    items: groups.get(t.value) || [],
  })).filter((g) => g.items.length > 0);
});

function formatTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(
    d.getHours()
  )}:${pad2(d.getMinutes())}`;
}
</script>

<template>
  <div class="modal-mask" :class="{ show }" @click.self="emit('close')">
    <div class="modal modal-wide" role="dialog" aria-modal="true" aria-label="AI 消费分析报告">
      <div class="section-head">
        <h3>AI 消费分析报告</h3>
        <span class="section-head__hint">
          由 DeepSeek 依据当期收支数据生成，支持月 / 季 / 半年 / 年四种周期；生成会产生少量 API 费用，归档后可随时回看
        </span>
      </div>

      <div class="report-toolbar">
        <select v-model="periodType" aria-label="周期类型" class="period-type">
          <option v-for="t in PERIOD_TYPES" :key="t.value" :value="t.value">{{ t.label }}</option>
        </select>

        <!-- 各类型独立的值输入器 -->
        <input v-if="periodType === 'month'" v-model="monthValue" type="month" aria-label="报告月份" />
        <template v-else-if="periodType === 'quarter'">
          <input v-model.number="quarterYear" type="number" min="2000" max="2100" aria-label="季度年份" />
          <select v-model="quarter" aria-label="季度">
            <option v-for="q in QUARTERS" :key="q" :value="q">Q{{ q }}</option>
          </select>
        </template>
        <template v-else-if="periodType === 'half'">
          <input v-model.number="halfYear" type="number" min="2000" max="2100" aria-label="半年度年份" />
          <select v-model="half" aria-label="半年度">
            <option v-for="h in HALVES" :key="h" :value="h">H{{ h }}</option>
          </select>
        </template>
        <input v-else v-model.number="yearValue" type="number" min="2000" max="2100" aria-label="年度" />

        <button class="btn primary" :disabled="busy" @click="generate">
          <AppIcon name="sparkles" :size="15" />
          {{ busy && isBusy("ai:report:generate") ? "生成中…" : "生成报告" }}
        </button>
        <button
          v-if="canArchive"
          class="btn right"
          :disabled="busy"
          @click="archive"
        >
          <AppIcon name="download" :size="15" />
          {{ busy && isBusy("ai:report:archive") ? "归档中…" : "归档" }}
        </button>
        <span v-if="preview && previewSource === 'archive'" class="hint archived-badge">
          <AppIcon name="check" :size="14" /> 已归档
        </span>
      </div>

      <div v-if="busy && isBusy('ai:report:generate')" class="report-view report-loading">
        正在分析 {{ periodValue }} 的账单数据，约需十几秒…
      </div>
      <div v-else-if="preview" class="report-view">
        <div v-if="preview.title" class="report-title">{{ preview.title }}</div>
        <!-- 内容由本组件转义后渲染，来源为后端生成的 Markdown -->
        <!-- eslint-disable-next-line vue/no-v-html -->
        <div class="report-md" v-html="reportHtml"></div>
        <details v-if="preview.context" class="report-source">
          <summary>数据来源附录（口径溯源）</summary>
          <pre>{{ JSON.stringify(preview.context, null, 2) }}</pre>
        </details>
      </div>
      <div v-else class="report-view report-loading">
        选择周期类型与时间后点击「生成报告」，DeepSeek 将基于该周期收支数据生成分析。
      </div>

      <!-- 归档列表：按周期类型分组，支持查看/删除 -->
      <div class="archive-section">
        <div class="section-head">
          <h4>归档报告</h4>
          <span class="section-head__hint">
            {{ archivedLoaded ? `共 ${archived.length} 份` : "加载中…" }}
          </span>
        </div>
        <div v-if="!archived.length" class="archive-empty">
          尚无归档报告。生成预览后点「归档」即可保存。
        </div>
        <div v-for="g in groupedArchived" :key="g.type" class="archive-group">
          <div class="archive-group-label">{{ g.label }}</div>
          <ul class="archive-list">
            <li v-for="item in g.items" :key="item.id">
              <span class="archive-title">{{ item.title }}</span>
              <span class="archive-meta">{{ formatTime(item.updated_at) }}</span>
              <span class="archive-actions">
                <button class="btn mini" @click="viewArchived(item)">查看</button>
                <button class="btn mini ghost" @click="removeArchived(item)">删除</button>
              </span>
            </li>
          </ul>
        </div>
      </div>

      <div class="modal-actions">
        <button class="btn ghost" @click="emit('close')">关闭</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Markdown 渲染后的排版 */
.report-md :deep(h2),
.report-md :deep(h3),
.report-md :deep(h4),
.report-md :deep(h5) {
  margin: var(--space-4) 0 var(--space-2);
  font-weight: 650;
  color: var(--color-text);
}
.report-md :deep(h2) {
  font-size: var(--text-lg);
}
.report-md :deep(h3) {
  font-size: var(--text-md);
  padding-left: var(--space-2-5);
  border-left: 3px solid var(--color-primary);
}
.report-md :deep(h4),
.report-md :deep(h5) {
  font-size: var(--text-base);
  color: var(--color-text-secondary);
}
.report-md :deep(h2:first-child),
.report-md :deep(h3:first-child) {
  margin-top: 0;
}
.report-md :deep(p) {
  margin: var(--space-2) 0;
  line-height: var(--leading-relaxed);
}
.report-md :deep(ul) {
  margin: var(--space-2) 0;
  padding-left: var(--space-5);
  list-style: disc;
}
.report-md :deep(li) {
  margin: var(--space-1) 0;
  line-height: var(--leading-relaxed);
  list-style: disc;
}
.report-md :deep(strong) {
  color: var(--color-text);
  font-weight: 650;
}
.report-md :deep(code) {
  padding: 1px 5px;
  border-radius: var(--radius-sm);
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-family: var(--font-mono);
  font-size: 0.92em;
}
.report-md :deep(hr) {
  margin: var(--space-4) 0;
  border: none;
  border-top: 1px solid var(--color-divider);
}
.report-title {
  margin-bottom: var(--space-3);
  font-size: var(--text-md);
  font-weight: 650;
  color: var(--color-text);
}
.report-source {
  margin-top: var(--space-4);
  border-top: 1px dashed var(--color-border);
  padding-top: var(--space-2);
}
.report-source summary {
  cursor: pointer;
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
  user-select: none;
}
.report-source summary:hover {
  color: var(--color-primary);
}
.report-source pre {
  margin: var(--space-2) 0 0;
  padding: var(--space-2);
  background: var(--color-bg-soft);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  line-height: var(--leading-relaxed);
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

/* 工具栏与周期输入器 */
.report-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}
.period-type {
  min-width: 88px;
}
.report-toolbar input[type="number"],
.report-toolbar select {
  width: auto;
  min-width: 72px;
  padding: 6px 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text);
  font-size: var(--text-base);
}
.archived-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--color-primary);
}

/* 归档列表 */
.archive-section {
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-divider);
}
.archive-empty {
  padding: var(--space-3) 0;
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}
.archive-group {
  margin-bottom: var(--space-3);
}
.archive-group-label {
  margin-bottom: var(--space-1);
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text-secondary);
}
.archive-list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.archive-list li {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1-5) var(--space-2);
  border-bottom: 1px solid var(--color-divider);
}
.archive-list li:last-child {
  border-bottom: none;
}
.archive-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text);
}
.archive-meta {
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
}
.archive-actions {
  display: flex;
  gap: var(--space-1);
}
@media (max-width: 860px) {
  .archive-list li {
    flex-wrap: wrap;
  }
  .archive-title {
    flex: 1 1 100%;
  }
}
</style>
