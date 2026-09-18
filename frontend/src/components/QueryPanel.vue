<script setup>
/* 对话式查账界面（T-6.2）：一句话提问 → 回答卡片（结论 + 分组占比 + 「依据」折叠区）。
 * 信任红线（与 T-6.1 一致）：
 *   - 口径必须展示：时间范围 / 筛选条件 / 覆盖笔数随每个答案给出，可被校验；
 *   - 查不到就说没查到（后端确定性生成，前端不加工数字）；
 *   - 降级 / 失败 / 越权都有人话提示，绝不静默假装正常。
 * 追问：保留最近 3 轮上下文回传，后端按指代规则继承口径（继承维度在卡片上标明）。 */
import { computed, nextTick, ref } from "vue";
import { askQuestion } from "../api/nl_query";
import { amountClass, fmtMoney, fmtSignedMoney, fmtType } from "../utils/format";
import { runTask } from "../composables/useLoading";
import { billsFilterHandoff, store } from "../store";
import { toast } from "../toast";
import AppIcon from "./AppIcon.vue";

const MAX_HISTORY = 3;
const EXAMPLES = ["本月餐饮支出多少", "上个月奶茶花了多少", "今年各分类支出排行", "最近30天花了多少"];

const question = ref("");
/* 会话：{ q, result?, error? } —— error 为请求失败的人话提示 */
const chat = ref([]);
const listEl = ref(null);

const busy = computed(() => chat.value.some((c) => c.pending));

const INHERITED_LABELS = { time: "时间", merchants: "商户", categories: "分类" };
const METRIC_LABELS = { expense: "支出", income: "收入", count: "笔数" };
const GROUP_LABELS = { category: "分类", merchant: "商户", month: "月份", day: "日期" };

function caliberParts(spec) {
  const parts = [spec.time.label || (spec.time.start ? `${spec.time.start} 至 ${spec.time.end}` : "全部时间")];
  if (spec.categories.length) parts.push(`分类 ${spec.categories.join("、")}`);
  if (spec.merchants.length) parts.push(`商户含 ${spec.merchants.join("、")}`);
  if (spec.group_by !== "none") parts.push(`按${GROUP_LABELS[spec.group_by] || spec.group_by}分组`);
  return parts;
}

function barWidth(rows, amount) {
  const max = Math.max(...rows.map((r) => Math.abs(Number(r.amount) || 0)), 1);
  return Math.max(2, Math.round((Math.abs(Number(amount) || 0) / max) * 100));
}

async function ask(text) {
  const q = (text ?? question.value).trim();
  if (!q || busy.value) return;
  question.value = "";
  const entry = { q, pending: true, result: null, error: "" };
  chat.value.push(entry);
  await nextTick();
  scrollBottom();
  const history = chat.value
    .filter((c) => c.result)
    .slice(-MAX_HISTORY)
    .map((c) => ({ question: c.q, spec: c.result.spec }));
  const res = await runTask({
    key: "query:ask",
    title: "查账中",
    detail: "正在解析问题并汇总…",
    rethrow: false,
    successText: "",
    task: async () => askQuestion(q, history),
  });
  entry.pending = false;
  if (res) {
    entry.result = res;
  } else {
    /* runTask rethrow:false 时失败返回 null；错误文案由 loading 层 toast 过，卡片内再留一份 */
    entry.error = "本次查询失败，请稍后重试；连续失败可在设置页检查模型配置。";
  }
  await nextTick();
  scrollBottom();
}

function scrollBottom() {
  const el = listEl.value;
  if (el) el.scrollTop = el.scrollHeight;
}

/* 存为筛选：把问账口径交给流水页复现（时间 + 收支类型 + 分类/商户多值） */
function saveAsFilter(result) {
  const spec = result.spec;
  billsFilterHandoff.value = {
    start: spec.time.start || "",
    end: spec.time.end || "",
    tx_type: spec.metric === "expense" || spec.metric === "income" ? spec.metric : "",
    categories: [...spec.categories],
    merchants: [...spec.merchants],
  };
  store.tab = "bills";
  toast("已按该口径切换到流水页");
}

function clearChat() {
  chat.value = [];
}
</script>

<template>
  <section class="panel" :class="{ active: store.tab === 'query' }">
    <div class="query-head">
      <div class="query-head__intro">
        <h3>一句话查账</h3>
        <p>答案附带完整口径（时间 / 筛选 / 覆盖笔数），可追问、可一键复现到流水页。</p>
      </div>
      <button v-if="chat.length" class="btn mini ghost" @click="clearChat">
        <AppIcon name="trash" :size="14" />
        清空
      </button>
    </div>

    <!-- 会话区 -->
    <div ref="listEl" class="query-list">
      <div v-if="!chat.length" class="query-empty">
        <p>试试这些问题：</p>
        <div class="query-examples">
          <button v-for="e in EXAMPLES" :key="e" class="btn mini ghost" @click="ask(e)">
            {{ e }}
          </button>
        </div>
      </div>

      <div v-for="(item, i) in chat" :key="i" class="query-turn">
        <div class="query-q">
          <span class="query-q__mark">问</span>
          <span class="query-q__text">{{ item.q }}</span>
        </div>

        <div v-if="item.pending" class="query-a query-a--pending">正在查询…</div>
        <div v-else-if="item.error" class="query-a query-a--error">
          <AppIcon name="alert" :size="15" />
          {{ item.error }}
        </div>
        <div v-else-if="item.result" class="query-a">
          <!-- 降级 / 越权等异常的人话提示（后端 message 原样展示） -->
          <div v-if="item.result.degraded" class="query-degraded">
            <AppIcon name="alert" :size="14" />
            {{ item.result.message || "本次由规则模式解析" }}
          </div>

          <p class="query-answer">{{ item.result.answer }}</p>

          <div class="query-caliber">
            <span v-for="(part, j) in caliberParts(item.result.spec)" :key="j" class="query-caliber__item">
              {{ part }}
            </span>
            <span class="query-caliber__item">
              {{ METRIC_LABELS[item.result.spec.metric] }} · 覆盖 {{ item.result.count }} 笔
            </span>
            <span
              v-if="item.result.inherited.length"
              class="query-caliber__item query-caliber__inherited"
              :title="'从上一问继承：' + item.result.inherited.map((k) => INHERITED_LABELS[k] || k).join('、')"
            >
              沿用上一问：{{ item.result.inherited.map((k) => INHERITED_LABELS[k] || k).join("、") }}
            </span>
          </div>

          <!-- 分组结果：CSS 占比条，无需画布 -->
          <ul v-if="item.result.grouped.length" class="query-grouped">
            <li v-for="row in item.result.grouped" :key="row.key">
              <span class="qg-name" :title="row.key">{{ row.key }}</span>
              <span class="qg-bar"><i :style="{ width: barWidth(item.result.grouped, row.amount) + '%' }"></i></span>
              <span class="qg-amount">{{ fmtMoney(row.amount) }}</span>
              <span class="qg-count">{{ row.count }}笔</span>
            </li>
          </ul>
          <p v-if="item.result.truncated" class="hint">仅显示金额最高的前 {{ item.result.grouped.length }} 组</p>

          <!-- 「依据」折叠区：参与计算的流水样本 -->
          <details v-if="item.result.details.length" class="query-details">
            <summary>依据（按金额降序的前 {{ item.result.details.length }} 笔样本）</summary>
            <table class="qg-table">
              <thead>
                <tr><th>时间</th><th>类型</th><th>商户</th><th>分类</th><th class="num">金额</th></tr>
              </thead>
              <tbody>
                <tr v-for="d in item.result.details" :key="d.id">
                  <td>{{ d.tx_time }}</td>
                  <td>{{ fmtType(d.tx_type) }}</td>
                  <td class="qg-name" :title="d.merchant">{{ d.merchant || "-" }}</td>
                  <td>{{ d.category }}</td>
                  <td class="num" :class="amountClass(d.tx_type)">{{ fmtSignedMoney(d.amount, d.tx_type) }}</td>
                </tr>
              </tbody>
            </table>
          </details>

          <div class="query-actions">
            <button class="btn mini" @click="saveAsFilter(item.result)">
              <AppIcon name="filter" :size="14" />
              存为筛选
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 输入区 -->
    <div class="query-input">
      <input
        v-model="question"
        type="text"
        placeholder="例如：上个月餐饮花了多少？支持追问，如「那收入呢」「按分类看看」"
        aria-label="查账问题"
        :disabled="busy"
        @keydown.enter="ask()"
      />
      <button class="btn primary" :disabled="busy || !question.trim()" @click="ask()">
        {{ busy ? "查询中…" : "提问" }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.query-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}
.query-head__intro h3 {
  margin: 0 0 2px;
}
.query-head__intro p {
  margin: 0;
  font-size: 0.85rem;
  color: var(--color-text-tertiary);
}
.query-list {
  display: grid;
  gap: var(--space-3);
  max-height: 56vh;
  overflow-y: auto;
  padding-right: var(--space-1);
}
.query-empty p {
  margin: 0 0 var(--space-2);
  color: var(--color-text-tertiary);
  font-size: 0.88rem;
}
.query-examples {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
}
.query-q {
  display: flex;
  align-items: center;
  gap: var(--space-1-5);
  margin-bottom: var(--space-1);
}
.query-q__mark {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-size: 0.75rem;
}
.query-q__text {
  font-weight: 600;
  word-break: break-all;
}
.query-a {
  margin-left: calc(22px + var(--space-1-5));
  padding: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  display: grid;
  gap: var(--space-1-5);
  background: var(--color-surface);
}
.query-a--pending,
.query-a--error {
  color: var(--color-text-tertiary);
  font-size: 0.9rem;
}
.query-a--error {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  color: var(--color-danger, var(--color-expense));
}
.query-degraded {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-1) var(--space-1-5);
  border-radius: 8px;
  background: var(--color-warn-soft, rgba(217, 155, 60, 0.12));
  color: var(--color-warn);
  font-size: 0.8rem;
}
.query-answer {
  margin: 0;
  font-size: 1.02rem;
  line-height: 1.6;
}
.query-caliber {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
}
.query-caliber__item {
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-size: 0.76rem;
  white-space: nowrap;
}
.query-caliber__inherited {
  background: transparent;
  border: 1px dashed var(--color-border);
  color: var(--color-text-tertiary);
}
.query-grouped {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 6px;
}
.query-grouped li {
  display: flex;
  align-items: center;
  gap: var(--space-1-5);
  font-size: 0.86rem;
}
.qg-name {
  flex: 0 1 26%;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.qg-bar {
  flex: 1;
  height: 8px;
  border-radius: 4px;
  background: var(--color-border);
  overflow: hidden;
}
.qg-bar i {
  display: block;
  height: 100%;
  border-radius: 4px;
  background: var(--color-primary);
}
.qg-amount {
  font-family: var(--font-numeric);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.qg-count {
  color: var(--color-text-tertiary);
  font-size: 0.78rem;
  white-space: nowrap;
}
.query-details summary {
  cursor: pointer;
  font-size: 0.82rem;
  color: var(--color-text-secondary);
}
.qg-table {
  width: 100%;
  margin-top: var(--space-1);
  border-collapse: collapse;
  font-size: 0.8rem;
}
.qg-table th,
.qg-table td {
  padding: 4px 8px;
  text-align: left;
  border-bottom: 1px solid var(--color-border);
  white-space: nowrap;
}
.qg-table .num {
  text-align: right;
  font-family: var(--font-numeric);
  font-variant-numeric: tabular-nums;
}
.query-actions {
  display: flex;
  justify-content: flex-end;
}
.query-input {
  display: flex;
  gap: var(--space-1-5);
  margin-top: var(--space-2);
}
.query-input input {
  flex: 1;
}
@media (max-width: 640px) {
  .query-list {
    max-height: 48vh;
  }
  .qg-name {
    flex: 0 1 32%;
  }
}
</style>
