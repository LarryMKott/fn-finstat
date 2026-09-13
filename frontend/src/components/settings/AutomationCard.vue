<script setup>
/* 设置-自动化：内置定时任务（账单自动导入等）的开关、执行间隔、
 * 手动立即执行与运行历史。任务列表由后端下发，前端只做展示与操作。 */
import { onMounted, reactive, ref } from "vue";
import {
  listAutomationTasks,
  runAutomationTask,
  toggleAutomationTask,
  updateAutomationInterval,
  listAutomationRuns,
} from "../../api/automation";
import { isBusy, runTask } from "../../composables/useLoading";
import { toast } from "../../toast";
import AppIcon from "../AppIcon.vue";

const tasks = ref([]);
/* 每任务的编辑态：editing=间隔编辑中；running / savingInterval 改由 loading 层的 key 锁派生 */
const uiState = reactive({});
/* 每任务展开的运行历史：{ loading, runs, error } */
const runViews = reactive({});
const expanded = ref({});

const loading = () => isBusy("automation:load");
const isRunning = (key) => isBusy(`automation:run:${key}`);
const isSavingInterval = (key) => isBusy(`automation:interval:${key}`);
const isLoadingRuns = (key) => isBusy(`automation:runs:${key}`);

function stateOf(key) {
  if (!uiState[key]) uiState[key] = { editing: false, interval: "" };
  return uiState[key];
}
function viewOf(key) {
  if (!runViews[key]) runViews[key] = { runs: [], loaded: false, error: "" };
  return runViews[key];
}

function fmtTime(epoch) {
  if (epoch === null || epoch === undefined) return "—";
  const d = new Date(epoch * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const STATUS_LABEL = { ok: "正常", failed: "失败", disabled: "已停用" };

async function load() {
  await runTask({
    key: "automation:load",
    title: "加载自动化任务",
    detail: "正在读取定时任务列表…",
    mode: "latest",
    rethrow: false,
    successText: (data) => `共 ${(data && data.tasks ? data.tasks.length : 0)} 个任务`,
    task: async () => {
      try {
        const data = await listAutomationTasks();
        tasks.value = data.tasks || [];
        for (const t of tasks.value) stateOf(t.task_key).interval = String(t.interval_minutes);
        return data;
      } catch (err) {
        throw new Error("自动化任务加载失败：" + err.message);
      }
    },
  });
}

async function toggle(t, ev) {
  const s = stateOf(t.task_key);
  /* 目标态先固化：successText 是延迟求值的，成功时 t.enabled 已被 Object.assign 覆盖，
   * 若在这里再读 t.enabled 会说出反的（刚启用却提示「已停用」） */
  const next = !t.enabled;
  let ok = false;
  await runTask({
    key: `automation:toggle:${t.task_key}`,
    title: next ? "启用定时任务" : "停用定时任务",
    detail: `正在更新「${t.name}」…`,
    rethrow: false,
    successText: () => `「${t.name}」已${next ? "启用" : "停用"}`,
    task: async () => {
      const updated = await toggleAutomationTask(t.task_key, next);
      Object.assign(t, updated);
      s.interval = String(t.interval_minutes);
      ok = true;
    },
  });
  /* 失败或被防重入拦截：数据没变，但原生 checkbox 已被用户点翻了。
   * 由于 :checked 绑定值未变化，Vue 不会重新 patch DOM，必须手动复位，
   * 否则界面显示「已启用」而后端仍是停用。 */
  if (!ok && ev && ev.target) ev.target.checked = !next;
}

async function saveInterval(t) {
  const s = stateOf(t.task_key);
  const n = Number(s.interval);
  if (!Number.isInteger(n) || n < 1 || n > 10080) {
    toast("执行间隔需为 1 ~ 10080 的整数分钟", true);
    return;
  }
  await runTask({
    key: `automation:interval:${t.task_key}`,
    title: "更新执行间隔",
    detail: `正在把「${t.name}」改为 ${n} 分钟…`,
    rethrow: false,
    /* 更新接口若返回空（异常路径已被 rethrow:false 吃掉），读属性会二次抛错
       并把一次成功的操作显示成失败 */
    successText: (updated) => `「${t.name}」执行间隔已更新为 ${updated?.interval_minutes ?? n} 分钟`,
    task: async () => {
      const updated = await updateAutomationInterval(t.task_key, n);
      Object.assign(t, updated);
      s.editing = false;
      s.interval = String(t.interval_minutes);
      return updated;
    },
  });
}

function cancelInterval(t) {
  const s = stateOf(t.task_key);
  s.editing = false;
  s.interval = String(t.interval_minutes);
}

async function runNow(t) {
  await runTask({
    key: `automation:run:${t.task_key}`,
    title: `执行「${t.name}」`,
    detail: "任务执行中，请稍候…",
    rethrow: false,
    successText: (r) => (r && r.ok ? "执行成功" : (r && r.message) || "执行失败"),
    task: async () => {
      const r = await runAutomationTask(t.task_key);
      if (t.last_status !== undefined) {
        t.last_status = r.ok ? "ok" : "failed";
        t.last_message = r.message;
      }
      const v = viewOf(t.task_key);
      if (v.loaded) loadRuns(t, true);
      return r;
    },
  });
}

async function loadRuns(t, force = false) {
  const v = viewOf(t.task_key);
  if (v.loaded && !force) return;
  await runTask({
    key: `automation:runs:${t.task_key}`,
    title: "加载运行历史",
    detail: `正在读取「${t.name}」的最近执行记录…`,
    mode: "latest",
    rethrow: false,
    successText: (data) => `共 ${(data && data.runs ? data.runs.length : 0)} 条记录`,
    task: async () => {
      try {
        const data = await listAutomationRuns(t.task_key, 20);
        v.runs = data.runs || [];
        v.loaded = true;
        v.error = "";
        return data;
      } catch (err) {
        v.error = err.message;
        throw err;
      }
    },
  });
}

async function toggleExpand(t) {
  expanded.value[t.task_key] = !expanded.value[t.task_key];
  if (expanded.value[t.task_key]) await loadRuns(t);
}

onMounted(load);
</script>

<template>
  <div class="settings-box">
    <div class="section-head">
      <h3>自动化</h3>
      <span class="section-head__hint">内置定时任务的启停与手动触发</span>
    </div>
    <p class="hint">
      任务按设定的间隔在后台自动执行；关闭后不再参与调度，可用「立即执行」手动触发一次。
    </p>

    <div v-if="loading()" class="hint">加载中…</div>
    <div v-else-if="!tasks.length" class="hint">暂无可用任务</div>

    <ul v-else class="auto-list">
      <li v-for="t in tasks" :key="t.task_key" class="auto-item">
        <div class="auto-item__head">
          <button
            class="auto-item__expand"
            :aria-expanded="!!expanded[t.task_key]"
            @click="toggleExpand(t)"
          >
            <AppIcon
              name="chevronDown"
              :size="14"
              class="auto-item__chevron"
              :class="{ open: expanded[t.task_key] }"
            />
            <span class="auto-item__name">{{ t.name }}</span>
          </button>
          <label class="switch-row">
            <input type="checkbox" :checked="t.enabled" @change="toggle(t, $event)" />
            <em>{{ t.enabled ? "已启用" : "已停用" }}</em>
          </label>
        </div>

        <div class="auto-item__meta">
          <span class="auto-item__status" :class="`auto-item__status--${t.last_status || 'none'}`">
            {{ STATUS_LABEL[t.last_status] || "尚未执行" }}
            <template v-if="t.last_message">：{{ t.last_message }}</template>
          </span>
          <span class="auto-item__next">下次执行：{{ fmtTime(t.next_run_at) }}</span>
        </div>

        <div class="auto-item__ops">
          <label class="auto-item__interval">
            间隔
            <template v-if="stateOf(t.task_key).editing">
              <input
                v-model="stateOf(t.task_key).interval"
                class="auto-item__interval-input"
                type="number"
                min="1"
                max="10080"
              />
              分钟
              <button
                class="btn mini"
                :disabled="isSavingInterval(t.task_key)"
                @click="saveInterval(t)"
              >
                {{ isSavingInterval(t.task_key) ? "保存中…" : "保存" }}
              </button>
              <button class="btn mini ghost" @click="cancelInterval(t)">取消</button>
            </template>
            <template v-else>
              <button class="auto-item__interval-view" @click="stateOf(t.task_key).editing = true">
                {{ t.interval_minutes }} 分钟
              </button>
            </template>
          </label>
          <button
            class="btn mini"
            :disabled="isRunning(t.task_key)"
            @click="runNow(t)"
          >
            <AppIcon name="recycle" :size="14" />
            {{ isRunning(t.task_key) ? "执行中…" : "立即执行" }}
          </button>
        </div>

        <div v-if="expanded[t.task_key]" class="auto-runs">
          <div v-if="isLoadingRuns(t.task_key)" class="hint">加载运行历史…</div>
          <div v-else-if="viewOf(t.task_key).error" class="fail">
            运行历史加载失败：{{ viewOf(t.task_key).error }}
          </div>
          <div v-else-if="!viewOf(t.task_key).runs.length" class="hint">暂无运行记录</div>
          <table v-else class="auto-runs__table">
            <thead>
              <tr>
                <th>开始时间</th>
                <th>结果</th>
                <th>影响条数</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in viewOf(t.task_key).runs" :key="r.id">
                <td class="tabular">{{ fmtTime(r.started_at) }}</td>
                <td>
                  <span :class="r.ok ? 'auto-runs__ok' : 'auto-runs__err'">
                    {{ r.ok ? "成功" : "失败" }}
                  </span>
                </td>
                <td class="tabular">{{ r.affected ?? "—" }}</td>
                <td class="auto-runs__msg">
                  {{ r.error || "—" }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.auto-list {
  list-style: none;
  margin: var(--space-3) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.auto-item {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 8px);
  padding: var(--space-3);
}
.auto-item__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}
.auto-item__expand {
  display: flex;
  align-items: center;
  gap: var(--space-1-5, 6px);
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  color: var(--color-text);
  font-weight: 550;
}
.auto-item__chevron {
  color: var(--color-text-tertiary);
  transition: transform 0.15s ease;
}
.auto-item__chevron.open {
  transform: rotate(180deg);
}
.auto-item__meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: var(--space-2);
  font-size: var(--text-xs);
}
.auto-item__status--ok {
  color: var(--color-success);
}
.auto-item__status--failed {
  color: var(--color-danger);
}
.auto-item__status--disabled,
.auto-item__status--none {
  color: var(--color-text-tertiary);
}
.auto-item__next {
  color: var(--color-text-tertiary);
}
.auto-item__ops {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2-5);
  flex-wrap: wrap;
  margin-top: var(--space-2);
}
.auto-item__interval {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}
.auto-item__interval-view {
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  color: var(--color-primary);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  text-decoration: underline dotted;
  text-underline-offset: 3px;
}
.auto-item__interval-input {
  width: 80px;
  font-variant-numeric: tabular-nums;
}
.auto-runs {
  margin-top: var(--space-3);
  border-top: 1px dashed var(--color-border);
  padding-top: var(--space-3);
  overflow-x: auto;
}
.auto-runs__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-xs);
}
.auto-runs__table th {
  text-align: left;
  color: var(--color-text-tertiary);
  font-weight: 500;
  padding: 4px 8px 4px 0;
  white-space: nowrap;
}
.auto-runs__table td {
  padding: 4px 8px 4px 0;
  vertical-align: top;
  color: var(--color-text-secondary);
}
.auto-runs__ok {
  color: var(--color-success);
}
.auto-runs__err {
  color: var(--color-danger);
}
.fail {
  color: var(--color-danger);
  font-size: var(--text-xs);
}
.auto-runs__msg {
  word-break: break-all;
}
</style>
