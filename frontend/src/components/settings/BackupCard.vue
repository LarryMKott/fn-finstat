<script setup>
/* 设置-备份与恢复：全量数据备份下载 + 从备份 JSON 恢复（合并/覆盖两种模式） */
import { computed, ref } from "vue";
import { backupDownloadUrl, restoreBackup } from "../../api/settings";
import { confirm } from "../../composables/useConfirm";
import { isBusy, runTask } from "../../composables/useLoading";
import { toast } from "../../toast";
import AppIcon from "../AppIcon.vue";

const emit = defineEmits(["restored"]);
const openNote = ref(false);
const restoreFile = ref(null);
const restoreReplace = ref(false);
const restoring = computed(() => isBusy("backup:restore"));
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
  const okToRestore = await confirm({
    title: "从备份恢复",
    message: msg,
    danger: restoreReplace.value,
    confirmText: restoreReplace.value ? "覆盖恢复" : "开始恢复",
  });
  if (!okToRestore) return;
  restoreResult.value = null;
  try {
    restoreResult.value = await runTask({
      key: "backup:restore",
      title: "从备份恢复",
      detail: `正在${restoreReplace.value ? "覆盖" : "合并"}导入 ${restoreFile.value.name}…`,
      successText: "恢复完成",
      task: async (update) => {
        const fd = new FormData();
        fd.append("file", restoreFile.value);
        fd.append("replace", restoreReplace.value ? "true" : "false");
        const res = await restoreBackup(fd);
        update("正在刷新数据概览…");
        emit("restored"); // 通知父级刷新「当前数据库」计数
        return res;
      },
    });
  } catch (err) {
    restoreResult.value = { ok: false, message: err.message };
  }
}
</script>

<template>
  <div class="settings-box">
    <div class="section-head">
      <h3>备份与恢复</h3>
      <button class="note-toggle" :aria-expanded="openNote" @click="openNote = !openNote">
        {{ openNote ? "收起说明" : "模式说明" }}
      </button>
    </div>
    <p class="hint">
      备份导出全部账号的流水、分类、预算与资产快照为一个 JSON 文件，建议定期下载保存。
    </p>
    <div v-show="openNote" class="note-box">
      <b>合并模式</b>（默认）：按唯一键去重导入，适合把多份备份汇总到同一个库。
      无交易号的流水无法去重，重复恢复同一备份可能产生重复记录。
      <b>覆盖模式</b>：先清空全部业务表再导入，不可恢复，请先下载一份当前备份。
    </div>
    <div class="settings-actions">
      <a class="btn primary" :href="backupDownloadUrl()">
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
</template>
