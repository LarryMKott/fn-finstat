<script setup>
/* 开放 API Token（T-1.2）：只读凭证管理。
 * 明文仅在签发响应中出现一次，服务端只存哈希；撤销立即失效。 */
import { computed, onMounted, reactive, ref } from "vue";
import { createToken, listTokens, revokeToken } from "../../api/tokens";
import { confirm } from "../../composables/useConfirm";
import { isBusy, runTask } from "../../composables/useLoading";
import { toast } from "../../toast";

const tokens = ref(null);
const newForm = reactive({ name: "" });
const freshToken = ref(""); // 仅签发后短暂展示

const hasFresh = computed(() => freshToken.value !== "");

async function load() {
  await runTask({
    key: "tokens:load",
    title: "加载 Token 列表",
    mode: "latest",
    silent: true,
    rethrow: false,
    task: async () => {
      tokens.value = await listTokens();
    },
  });
}

async function doCreate() {
  if (!newForm.name.trim()) {
    toast("请填写 Token 名称", true);
    return;
  }
  await runTask({
    key: "tokens:create",
    title: "签发 Token",
    rethrow: false,
    task: async () => {
      const created = await createToken(newForm.name.trim());
      freshToken.value = created.token;
      newForm.name = "";
      await load();
    },
  });
}

function copyFresh() {
  const code = freshToken.value;
  if (!code) return;
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(code).then(
      () => toast("已复制到剪贴板"),
      () => toast("复制失败，请手动选择复制", true),
    );
    return;
  }
  // http 内网访问时无 clipboard API（非安全上下文），退回 execCommand
  const input = document.createElement("textarea");
  input.value = code;
  document.body.appendChild(input);
  input.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  input.remove();
  toast(ok ? "已复制到剪贴板" : "复制失败，请手动选择复制", !ok);
}

async function doRevoke(t) {
  const ok = await confirm({
    title: "撤销 Token",
    message: `撤销「${t.name}」（${t.token_prefix}…）？使用该 Token 的脚本将立即失效。`,
    danger: true,
    confirmText: "撤销",
  });
  if (!ok) return;
  await runTask({
    key: `tokens:revoke:${t.id}`,
    title: "撤销 Token",
    rethrow: false,
    successText: "已撤销",
    task: async () => {
      await revokeToken(t.id);
      await load();
    },
  });
}

function fmtTime(epoch) {
  if (!epoch) return "—";
  const d = new Date(epoch * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

defineExpose({ load });
onMounted(load);
</script>

<template>
  <div class="settings-box">
    <div class="section-head">
      <h3>开放 API Token</h3>
      <span class="section-head__hint">
        只读访问凭证：供脚本 / 第三方工具查询你的数据；不能写入，也到不了管理面
      </span>
    </div>

    <div class="filter-bar token-add">
      <input
        v-model="newForm.name"
        type="text"
        placeholder="Token 名称，如：home-assistant"
        aria-label="Token 名称"
        @keydown.enter="doCreate"
      />
      <button class="btn mini primary" :disabled="isBusy('tokens:create')" @click="doCreate">
        签发 Token
      </button>
    </div>

    <div v-if="hasFresh" class="token-fresh">
      <p>请立即保存（仅此一次展示）：</p>
      <code class="token-plain">{{ freshToken }}</code>
      <button class="btn mini" @click="copyFresh">复制</button>
      <button class="btn mini ghost" @click="freshToken = ''">我已保存</button>
    </div>

    <table v-if="tokens && tokens.length" class="token-table">
      <thead>
        <tr>
          <th>名称</th>
          <th>前缀</th>
          <th>创建</th>
          <th>最近使用</th>
          <th>状态</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="t in tokens" :key="t.id">
          <td>{{ t.name }}</td>
          <td class="token-mono">{{ t.token_prefix }}…</td>
          <td>{{ fmtTime(t.created_at) }}</td>
          <td>{{ t.last_used_at ? fmtTime(t.last_used_at) : "从未" }}</td>
          <td>
            <span class="token-state" :class="t.revoked ? 'st-warn' : 'st-ok'">
              {{ t.revoked ? "已撤销" : "有效" }}
            </span>
          </td>
          <td class="num">
            <button v-if="!t.revoked" class="btn mini danger" @click="doRevoke(t)">撤销</button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else-if="tokens" class="hint">还没有 Token</p>
  </div>
</template>

<style scoped>
.token-add input[type="text"] {
  min-width: 16em;
}
.token-fresh {
  border: 1px solid rgba(232, 131, 58, 0.5);
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 8px;
}
.token-fresh p {
  margin: 0 0 4px;
  font-size: 13px;
}
.token-plain {
  display: inline-block;
  font-family: monospace;
  margin-right: 8px;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.06);
  user-select: all;
}
.token-table {
  width: 100%;
  font-size: 13px;
  border-collapse: collapse;
}
.token-table th,
.token-table td {
  text-align: left;
  padding: 4px 8px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}
.token-mono {
  font-family: monospace;
}
.token-state.st-ok {
  color: #2e8b57;
}
.token-state.st-warn {
  color: #d64545;
}
</style>
