<script setup>
import { ref } from "vue";
import { api } from "../api";
import { store } from "../store";
import { toast } from "../toast";

const fileWechat = ref(null);
const fileAlipay = ref(null);
const files = ref({ wechat: null, alipay: null });
const busy = ref({ wechat: false, alipay: false });
const importResult = ref(null);

function onFileChange(kind, e) {
  files.value[kind] = e.target.files[0] || null;
}

async function upload(kind) {
  const file = files.value[kind];
  const input = kind === "wechat" ? fileWechat.value : fileAlipay.value;
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
    input.value = "";
    files.value[kind] = null;
  } catch (err) {
    importResult.value = { ok: false, message: err.message };
  } finally {
    busy.value[kind] = false;
  }
}
</script>

<template>
  <section class="panel" :class="{ active: store.tab === 'import' }">
    <div class="import-grid">
      <div class="import-box">
        <h3>微信支付账单（xlsx）</h3>
        <p class="hint">微信 → 我 → 服务 → 钱包 → 账单 → 右上角「账单下载」→ 导出 Excel</p>
        <input ref="fileWechat" type="file" accept=".xlsx" @change="onFileChange('wechat', $event)" />
        <button class="btn primary" :disabled="busy.wechat" @click="upload('wechat')">上传并导入</button>
      </div>
      <div class="import-box">
        <h3>支付宝账单（csv）</h3>
        <p class="hint">支付宝 → 我的 → 账单 → 交易流水证明 → 导出 CSV 流水</p>
        <input ref="fileAlipay" type="file" accept=".csv" @change="onFileChange('alipay', $event)" />
        <button class="btn primary" :disabled="busy.alipay" @click="upload('alipay')">上传并导入</button>
      </div>
    </div>
    <div class="import-result" :class="{ show: !!importResult }">
      <template v-if="importResult">
        <div v-if="importResult.ok" class="ok">导入完成</div>
        <div v-if="importResult.ok">
          解析 {{ importResult.total }} 条 · 新增入库 <b>{{ importResult.inserted }}</b> 条 ·
          跳过重复 <b>{{ importResult.skipped }}</b> 条
        </div>
        <div v-else :style="{ color: 'var(--expense)' }">导入失败：{{ importResult.message }}</div>
      </template>
    </div>
  </section>
</template>
