<script setup>
/* AI 月度消费报告弹窗：选择月份 → DeepSeek 生成 Markdown 报告。
 * 改造点：原实现把 Markdown 原文塞进 <pre> 直接展示，标题/列表/粗体都不生效。
 * 这里做轻量渲染（标题、无序列表、粗体、分隔线），不引入额外依赖。 */
import { computed, ref } from "vue";
import { aiMonthReport } from "../api/ai";
import { previousMonth } from "../utils/datetime";
import { isBusy, runTask } from "../composables/useLoading";
import AppIcon from "./AppIcon.vue";

defineProps({ show: Boolean });
const emit = defineEmits(["close"]);

const month = ref(previousMonth());
const busy = computed(() => isBusy("ai:month-report"));
const report = ref("");
const reportMonth = ref("");

/* 打开时不自动调用：生成请求会产生 DeepSeek API 费用，必须由用户显式点击触发 */

async function generate() {
  // 不在请求前清空旧报告：失败（rethrow:false 返回 undefined）时保留上一份，避免用户误删
  const res = await runTask({
    key: "ai:month-report",
    title: "生成 AI 月度报告",
    detail: `正在分析 ${month.value} 的收支数据…`,
    rethrow: false,
    successText: "报告已生成",
    task: () => aiMonthReport(month.value),
  });
  if (!res) return;
  report.value = res.report;
  reportMonth.value = res.month;
}

/* 极简 Markdown → HTML：只处理报告实际用到的语法，避免引入渲染库增大包体。
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
  const src = escapeHtml(report.value || "").replace(/\r\n/g, "\n");
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
</script>

<template>
  <div class="modal-mask" :class="{ show }" @click.self="emit('close')">
    <div class="modal modal-wide" role="dialog" aria-modal="true" aria-label="AI 月度消费报告">
      <div class="section-head">
        <h3>AI 月度消费报告</h3>
        <span class="section-head__hint">
          由 DeepSeek 依据当月收支数据生成，含结构亮点与下月建议；调用会产生少量 API 费用
        </span>
      </div>

      <div class="report-toolbar">
        <input v-model="month" type="month" aria-label="报告月份" />
        <button class="btn primary" :disabled="busy" @click="generate">
          <AppIcon name="sparkles" :size="15" />
          {{ busy ? "生成中…" : report ? "重新生成" : "生成报告" }}
        </button>
        <span v-if="reportMonth && !busy" class="hint">报告月份：{{ reportMonth }}</span>
      </div>

      <div v-if="busy" class="report-view report-loading">
        正在分析 {{ month }} 的账单数据，约需十几秒…
      </div>
      <div v-else-if="report" class="report-view">
        <!-- 内容由本组件转义后渲染，来源为后端生成的 Markdown -->
        <!-- eslint-disable-next-line vue/no-v-html -->
        <div class="report-md" v-html="reportHtml"></div>
      </div>
      <div v-else class="report-view report-loading">
        选择月份后点击「生成报告」，DeepSeek 将基于该月收支数据生成分析。
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
</style>
