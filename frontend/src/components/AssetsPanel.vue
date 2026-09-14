<script setup>
/* 资产管理：资产/负债快照记录 + 净资产趋势图（按当前飞牛账号隔离）。
 * 改造点：表单改为带标签的栅格布局并在移动端单列；趋势图统一从设计令牌取色 */
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { assetTrend, createAsset, deleteAsset, listAssets, updateAsset } from "../api/asset";
import { axisBase, chartBase, chartTokens } from "../utils/chartTheme";
import { fmtMoney } from "../utils/format";
import { todayStr } from "../utils/datetime";
import { useChart } from "../composables/useChart";
import { confirm } from "../composables/useConfirm";
import { isBusy, runTask } from "../composables/useLoading";
import { store } from "../store";
import { toast } from "../toast";
import AppIcon from "./AppIcon.vue";

const snapshots = ref([]);
const editingId = ref(null);
const form = reactive({ snap_date: "", name: "", asset_type: "asset", amount: "", remark: "" });

const chartEl = ref(null);

const trendChart = useChart(chartEl, (chart) => renderTrendChart(chart), {
  /* 面板隐藏时容器 display:none，主题重绘跳过；切回面板会重新 load */
  canRender: () => store.tab === "assets",
});

/* 提交/删除期间锁住按钮，避免连点产生重复快照 */
const saving = computed(() => isBusy("assets:submit") || isBusy("assets:delete"));

async function load() {
  await runTask({
    key: "assets:load",
    title: "加载资产快照",
    detail: "正在读取快照列表…",
    mode: "latest",
    rethrow: false,
    successText: (rows) => `共 ${(rows || []).length} 条快照`,
    task: async () => {
      try {
        snapshots.value = await listAssets();
      } catch (err) {
        throw new Error("资产数据加载失败：" + err.message);
      }
      await nextTick();
      renderTrend();
      return snapshots.value;
    },
  });
}

async function loadTrend() {
  return runTask({
    key: "assets:trend",
    title: "加载净资产趋势",
    detail: "正在汇总各期资产与负债…",
    mode: "latest",
    rethrow: false,
    successText: "趋势已更新",
    task: async () => {
      try {
        return await assetTrend();
      } catch (err) {
        throw new Error("净资产趋势加载失败：" + err.message);
      }
    },
  });
}

/* 最近一次成功数据：主题切换重绘时直接复用，不必重新请求 */
const lastTrend = ref([]);

async function renderTrend() {
  const trend = await loadTrend();
  /* 失败时 loadTrend 返回 undefined，保留上一次数据，不把图表清空 */
  if (trend) lastTrend.value = trend;
  trendChart.render();
}

function renderTrendChart(chart) {
  const t = chartTokens();
  const axis = axisBase();
  const base = chartBase();
  const trend = lastTrend.value;
  chart.setOption(
    {
      ...base,
      legend: {
        data: ["资产", "负债", "净资产"],
        textStyle: { color: t.subtext, fontSize: 12 },
        icon: "roundRect",
        itemWidth: 10,
        itemHeight: 10,
        top: 0,
        right: 0,
      },
      tooltip: { ...base.tooltip, trigger: "axis" },
      xAxis: { type: "category", data: trend.map((x) => x.date), ...axis, splitLine: { show: false } },
      yAxis: {
        type: "value",
        ...axis,
        axisLine: { show: false },
        axisLabel: { formatter: (v) => "¥" + v, color: t.subtext, fontSize: 11 },
      },
      series: [
        {
          name: "资产",
          type: "line",
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 2.2, color: t.expense },
          itemStyle: { color: t.expense },
          data: trend.map((x) => x.assets),
        },
        {
          name: "负债",
          type: "line",
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 2.2, color: t.income },
          itemStyle: { color: t.income },
          data: trend.map((x) => x.liabilities),
        },
        {
          name: "净资产",
          type: "line",
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 2.6, color: t.primary },
          itemStyle: { color: t.primary },
          areaStyle: {
            opacity: 0.1,
            color: {
              type: "linear",
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: t.primary },
                { offset: 1, color: "transparent" },
              ],
            },
          },
          data: trend.map((x) => x.net),
        },
      ],
    },
    true,
  );
}

function resetForm() {
  Object.assign(form, { snap_date: todayStr(), name: "", asset_type: "asset", amount: "", remark: "" });
  editingId.value = null;
}

function edit(row) {
  editingId.value = row.id;
  Object.assign(form, {
    snap_date: row.snap_date,
    name: row.name,
    asset_type: row.asset_type,
    amount: row.amount,
    remark: row.remark || "",
  });
}

async function submit() {
  const amount = Number(form.amount);
  if (!form.snap_date) return toast("请选择快照日期", true);
  if (!form.name.trim()) return toast("请填写账户/条目名称", true);
  if (!isFinite(amount) || amount < 0) return toast("请输入有效金额", true);
  const payload = {
    snap_date: form.snap_date,
    name: form.name.trim(),
    asset_type: form.asset_type,
    amount,
    remark: form.remark.trim(),
  };
  const res = await runTask({
    key: "assets:submit",
    title: editingId.value ? "更新快照" : "记录快照",
    detail: `正在保存「${payload.name}」…`,
    rethrow: false,
    successText: editingId.value ? "快照已更新" : "快照已记录",
    task: () => (editingId.value ? updateAsset(editingId.value, payload) : createAsset(payload)),
  });
  if (!res) return;
  resetForm();
  load();
}

async function remove(row) {
  if (!(await confirm({ title: "删除快照", message: `删除 ${row.snap_date} 的「${row.name}」快照吗？`, danger: true, confirmText: "删除" }))) return;
  /* DELETE 返回 204，runTask 会解析成 null；用 done 标记成功而不是用返回值真假，
     否则删完不刷新。失败时 done 仍为 false，不做无谓的重新加载。 */
  let done = false;
  await runTask({
    key: "assets:delete",
    title: "删除快照",
    detail: `正在删除「${row.name}」…`,
    rethrow: false,
    successText: "已删除",
    task: async () => {
      await deleteAsset(row.id);
      done = true;
      /* 编辑态挂在被删快照上时同步复位，否则「保存修改」会对已删 id 发 PUT */
      if (editingId.value === row.id) resetForm();
      return { deleted: true };
    },
  });
  if (done) load();
}

watch(
  () => store.tab === "assets",
  (active) => {
    if (active) load();
  },
  { immediate: true },
);

onMounted(resetForm);
</script>

<template>
  <section class="panel" :class="{ active: store.tab === 'assets' }">
    <div class="chart-box">
      <div class="section-head">
        <h3>净资产趋势</h3>
        <span class="section-head__hint">净资产 = 资产 - 负债，按快照日期汇总</span>
      </div>
      <div ref="chartEl" class="chart"></div>
    </div>

    <div class="chart-box">
      <div class="section-head">
        <h3>{{ editingId ? "编辑快照" : "记录快照" }}</h3>
        <span class="section-head__hint">
          建议每月固定日期记录一次存款、理财、房贷等余额，负债金额记正数
        </span>
      </div>
      <div class="asset-form">
        <label>日期<input v-model="form.snap_date" type="date" /></label>
        <label>账户/条目<input v-model="form.name" type="text" maxlength="32" placeholder="如：招商储蓄卡 / 房贷余额" /></label>
        <label>类型
          <select v-model="form.asset_type">
            <option value="asset">资产</option>
            <option value="liability">负债</option>
          </select>
        </label>
        <label>金额（元）<input v-model="form.amount" type="number" step="0.01" min="0" placeholder="0.00" /></label>
        <label>备注<input v-model="form.remark" type="text" maxlength="100" placeholder="可选" /></label>
        <div class="asset-form-actions">
          <button class="btn primary" :disabled="saving" @click="submit">
            <AppIcon v-if="!editingId" name="plus" :size="15" />
            {{ editingId ? "保存修改" : "记录" }}
          </button>
          <button v-if="editingId" class="btn ghost" @click="resetForm">取消</button>
        </div>
      </div>
    </div>

    <div class="table-wrap">
      <div class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th>日期</th>
              <th>账户/条目</th>
              <th>类型</th>
              <th class="num">金额</th>
              <th>备注</th>
              <th class="col-ops"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!snapshots.length">
              <td colspan="6" class="empty">还没有资产快照，先在上方记录一条</td>
            </tr>
            <tr v-for="s in snapshots" :key="s.id">
              <td class="cell-time">{{ s.snap_date }}</td>
              <td class="cell-primary">{{ s.name }}</td>
              <td>
                <span class="tag" :class="s.asset_type === 'asset' ? 'tag-income' : 'tag-expense'">
                  {{ s.asset_type === "asset" ? "资产" : "负债" }}
                </span>
              </td>
              <td class="num money">{{ fmtMoney(s.amount) }}</td>
              <td class="cell-secondary" :title="s.remark">{{ s.remark || "—" }}</td>
              <td class="col-ops">
                <button class="btn link" @click="edit(s)">编辑</button>
                <button class="btn link danger" :disabled="saving" @click="remove(s)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>

<style scoped>
.col-ops {
  width: 110px;
  text-align: right;
}
</style>
