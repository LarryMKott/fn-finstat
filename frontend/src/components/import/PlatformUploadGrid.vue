<script setup>
/* 四平台手动上传卡片：微信 xlsx / 支付宝·京东·云闪付 csv。
 * 平台信息来自 utils/constants.PLATFORMS；导入结果通过 result 事件交给父组件展示。 */
import { ref } from "vue";
import { uploadBillFile } from "../../api/upload";
import { PLATFORMS } from "../../utils/constants";
import { isBusy, runTask } from "../../composables/useLoading";
import { toast } from "../../toast";
import AppIcon from "../AppIcon.vue";

const emit = defineEmits(["result"]);

const files = ref(Object.fromEntries(PLATFORMS.map((p) => [p.key, null])));
const inputs = ref({});

const PLATFORM_TITLE = Object.fromEntries(PLATFORMS.map((p) => [p.key, p.title]));

/* 每个平台一把锁：同一平台连点会被拦截，不同平台互不阻塞 */
const busy = (kind) => isBusy(`upload:${kind}`);

function onFileChange(kind, e) {
  files.value[kind] = e.target.files[0] || null;
}

async function upload(kind) {
  const file = files.value[kind];
  if (!file) {
    toast("请先选择文件", true);
    return;
  }
  emit("result", null);
  try {
    await runTask({
      key: `upload:${kind}`,
      title: `导入${PLATFORM_TITLE[kind] || "账单"}`,
      detail: `正在上传并解析 ${file.name}…`,
      successText: (r) => `导入完成：新增 ${r ? r.inserted : 0} 条`,
      task: async (update) => {
        const fd = new FormData();
        fd.append("file", file);
        const data = await uploadBillFile(kind, fd);
        update("正在写库，请稍候…");
        emit("result", { ok: true, ...data });
        const input = inputs.value[kind];
        if (input) input.value = "";
        files.value[kind] = null;
        return data;
      },
    });
  } catch (err) {
    emit("result", { ok: false, message: err.message });
  }
}
</script>

<template>
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
      <button class="btn primary" :disabled="busy(p.key)" @click="upload(p.key)">
        <AppIcon name="import" :size="15" />
        {{ busy(p.key) ? "导入中…" : "上传并导入" }}
      </button>
    </div>
  </div>
</template>
