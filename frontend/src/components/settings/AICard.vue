<script setup>
/* 设置-智能分类（DeepSeek）：API Key、模型、自动归类开关与连通性测试 */
import { computed, onMounted, reactive, ref } from "vue";
import { aiConfig, saveAIConfig, testAI } from "../../api/ai";
import { isBusy, runTask } from "../../composables/useLoading";

const AI_MODELS = [
  { value: "deepseek-chat", label: "deepseek-chat（V3，推荐）" },
  { value: "deepseek-reasoner", label: "deepseek-reasoner（R1，较慢较贵）" },
];

const openNote = ref(false);
const ai = reactive({ enabled: false, api_key: "", base_url: "", model: "deepseek-chat" });
const aiInfo = ref(null);
/* 忙标记统一由 loading 层的 key 锁派生 */
const aiSaving = computed(() => isBusy("ai:save"));
const aiTesting = computed(() => isBusy("ai:test"));
const aiTestResult = ref(null);

async function loadAI() {
  await runTask({
    key: "ai:load",
    title: "读取智能分类配置",
    detail: "正在加载 DeepSeek 配置…",
    mode: "latest",
    rethrow: false,
    successText: "配置已加载",
    task: async () => {
      try {
        aiInfo.value = await aiConfig();
      } catch (err) {
        throw new Error("智能分类配置加载失败：" + err.message);
      }
      ai.enabled = aiInfo.value.enabled;
      ai.base_url = aiInfo.value.base_url;
      ai.model = aiInfo.value.model;
    },
  });
}

function aiPayload() {
  /* 密钥仅在表单里输入了新值时才提交（后端约定：不传 = 保持已保存的密钥） */
  const payload = { enabled: ai.enabled, base_url: ai.base_url, model: ai.model };
  if (ai.api_key.trim()) payload.api_key = ai.api_key.trim();
  return payload;
}

async function saveAI() {
  await runTask({
    key: "ai:save",
    title: "保存智能分类配置",
    detail: "正在写入配置…",
    rethrow: false,
    successText: "智能分类配置已保存",
    task: async () => {
      aiInfo.value = await saveAIConfig(aiPayload());
      ai.api_key = "";
    },
  });
}

async function testConnection() {
  aiTestResult.value = null;
  try {
    aiTestResult.value = await runTask({
      key: "ai:test",
      title: "测试 DeepSeek 连接",
      detail: "正在调用模型接口…",
      successText: (r) => (r && r.ok ? "连接成功" : (r && r.message) || "连接失败"),
      task: () => testAI(aiPayload()),
    });
  } catch (err) {
    /* 失败原因已在进度浮层展示，这里同步写回卡片内的结果区 */
    aiTestResult.value = { ok: false, message: err.message };
  }
}

onMounted(loadAI);
</script>

<template>
  <div class="settings-box">
    <div class="section-head">
      <h3>智能分类（DeepSeek）</h3>
      <button class="note-toggle" :aria-expanded="openNote" @click="openNote = !openNote">
        {{ openNote ? "收起说明" : "配置说明" }}
      </button>
    </div>
    <p class="hint">
      配置 API Key 后，关键词未命中的流水会交给大模型语义归类；调用会产生少量 API 费用。
    </p>
    <div v-show="openNote" class="note-box">
      在
      <a href="https://platform.deepseek.com" target="_blank" rel="noopener">platform.deepseek.com</a>
      创建 API Key 后填入下方并测试连接。开启「导入时自动智能分类」后，导入账单先走内置关键词归类，
      仍未命中的记录自动交给 DeepSeek；也可在「流水」页点击「AI 智能分类」批量重归类存量流水（单次最多 1000 条）。
      AI 只能返回当前分类表中已有的名称，返回编造分类会被丢弃并保留原分类。
    </div>
    <div class="form-grid">
      <label class="field">
        API Key
        <input
          v-model="ai.api_key"
          type="password"
          :placeholder="aiInfo?.has_api_key ? `已设置（${aiInfo.api_key_hint}），留空保持不变` : 'sk-...'"
          autocomplete="new-password"
        />
      </label>
      <label class="field">
        模型
        <select v-model="ai.model">
          <option v-for="m in AI_MODELS" :key="m.value" :value="m.value">{{ m.label }}</option>
        </select>
      </label>
      <label class="field">
        API 地址
        <input v-model="ai.base_url" type="text" placeholder="https://api.deepseek.com" />
      </label>
      <div class="field">
        <span class="switch-grid-label">导入时自动智能分类</span>
        <label class="switch-row">
          <input v-model="ai.enabled" type="checkbox" />
          <em>{{ ai.enabled ? "已开启：仅对关键词未命中的「其他」流水调用" : "已关闭：仅使用内置关键词归类" }}</em>
        </label>
      </div>
    </div>
    <div class="settings-actions">
      <button class="btn" :disabled="aiTesting || aiSaving" @click="testConnection">
        {{ aiTesting ? "测试中…" : "测试连接" }}
      </button>
      <button class="btn primary" :disabled="aiTesting || aiSaving" @click="saveAI">
        {{ aiSaving ? "保存中…" : "保存配置" }}
      </button>
    </div>
    <div class="settings-result" :class="{ show: !!aiTestResult, ok: aiTestResult?.ok, err: aiTestResult && !aiTestResult.ok }">
      <template v-if="aiTestResult">
        <template v-if="aiTestResult.ok"><b>连接成功</b>，可以开始智能分类</template>
        <template v-else>连接失败：{{ aiTestResult.message }}</template>
      </template>
    </div>
  </div>
</template>
