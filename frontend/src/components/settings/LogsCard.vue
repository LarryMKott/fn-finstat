<script setup>
/* 设置-运行日志：尾部查看（行数可选）、刷新与完整日志下载 */
import { computed, ref, watch } from "vue";
import { logsDownloadUrl, runtimeLogs } from "../../api/settings";
import { fmtSize } from "../../utils/format";
import { store } from "../../store";
import { isBusy, runTask } from "../../composables/useLoading";
import AppIcon from "../AppIcon.vue";

const logLines = ref(300);
const logInfo = ref(null);
const logLoading = computed(() => isBusy("logs:load"));
const logLoaded = ref(false);

async function loadLogs() {
  await runTask({
    key: "logs:load",
    title: "读取运行日志",
    detail: `正在加载最近 ${logLines.value} 行…`,
    mode: "latest",
    rethrow: false,
    successText: (info) => `已加载 ${info ? info.lines : 0} 行`,
    task: async () => {
      try {
        logInfo.value = await runtimeLogs(logLines.value);
      } catch (err) {
        throw new Error("运行日志加载失败：" + err.message);
      }
      logLoaded.value = true;
      return logInfo.value;
    },
  });
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
      <a class="btn" :href="logsDownloadUrl()">
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
</template>
