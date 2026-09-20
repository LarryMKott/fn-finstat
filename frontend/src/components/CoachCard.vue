<script setup>
/* AI 财务教练（T-1.6）：基于结构化摘要的对话式建议。
 * 隐私：只发送聚合数据（月均收支 / 分类占比），不外传任何单笔流水。 */
import { nextTick, reactive, ref } from "vue";
import { coachChat } from "../api/coach";
import { isBusy, runTask } from "../composables/useLoading";
import { toast } from "../toast";
import AppIcon from "./AppIcon.vue";

const messages = reactive([]); // { role: 'user'|'coach', text }
const input = ref("");
const showContext = ref(-1); // 展开口径的消息序号，-1 = 全收起
const chatBody = ref(null);

const busy = computed(() => isBusy("coach:chat"));

const visibleMessages = computed(() =>
  messages.value.filter((m) => m.role !== "context"),
);

async function send() {
  const q = input.value.trim();
  if (!q || busy.value) return;
  if (q.length > 500) {
    toast("问题过长，请精简后重试（500 字以内）", true);
    return;
  }
  input.value = "";
  messages.value.push({ role: "user", text: q });

  // 追问上下文：最近 3 轮 user + coach 问答
  const qa = messages.value.filter((m) => m.role !== "context");
  const history = [];
  for (let i = 0; i < qa.length - 1 && history.length < 3; i += 2) {
    if (qa[i]?.role === "user" && qa[i + 1]?.role === "coach") {
      history.push({ question: qa[i].text, answer: qa[i + 1].text });
    }
  }

  messages.value.push({ role: "coach", text: "" });
  const idx = messages.value.length - 1;

  await runTask({
    key: "coach:chat",
    title: "AI 教练思考中",
    rethrow: false,
    task: async () => {
      try {
        const res = await coachChat(q, history);
        messages.value[idx] = { role: "coach", text: res.answer, context: res.context };
      } catch (err) {
        messages.value[idx] = { role: "coach", text: err.message || "教练暂时无法回答，请稍后再试", isError: true };
      }
      await nextTick();
      scrollToBottom();
    },
  });
  await nextTick();
  scrollToBottom();
}

function scrollToBottom() {
  if (chatBody.value) chatBody.value.scrollTop = chatBody.value.scrollHeight;
}
</script>

<template>
  <div class="chart-box">
    <div class="section-head">
      <h3>AI 财务教练</h3>
      <span class="section-head__hint">
        基于你的结构化财务摘要给出建议；原始流水不外传，口径透明可展开
      </span>
    </div>

    <div ref="chatBody" class="coach-chat-body">
      <div v-if="!visibleMessages.length" class="empty">
        问问你的财务教练，如「我的消费结构健康吗？」「怎么才能多存钱？」
      </div>
      <div
        v-for="(m, idx) in visibleMessages"
        :key="idx"
        class="coach-msg"
        :class="m.role === 'user' ? 'coach-msg--user' : 'coach-msg--coach'"
      >
        <div class="coach-msg__bubble" :class="{ 'coach-msg__error': m.isError }">
          <span class="coach-msg__text">{{ m.text }}</span>
          <button
            v-if="m.context && m.role === 'coach'"
            class="btn mini ghost coach-msg__caliber"
            @click="showContext = showContext === idx ? -1 : idx"
          >
            {{ showContext === idx ? "收起口径" : "口径" }}
          </button>
          <pre v-if="m.context && showContext === idx" class="coach-msg__context">{{ m.context }}</pre>
        </div>
      </div>
    </div>

    <div class="coach-input-bar">
      <input
        v-model="input"
        type="text"
        placeholder="问你的财务教练…（≤500 字）"
        aria-label="财务问题"
        maxlength="500"
        @keydown.enter="send"
      />
      <button
        class="btn mini primary"
        :disabled="busy || !input.trim()"
        @click="send"
      >
        <AppIcon name="sparkles" :size="14" />
        {{ busy ? "思考中…" : "提问" }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.coach-chat-body {
  max-height: 320px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 4px 0;
}
.coach-msg {
  display: flex;
}
.coach-msg--user {
  justify-content: flex-end;
}
.coach-msg--coach {
  justify-content: flex-start;
}
.coach-msg__bubble {
  max-width: 85%;
  padding: 6px 10px;
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.coach-msg--user .coach-msg__bubble {
  background: var(--accent, #e8833a);
  color: #fff;
}
.coach-msg--coach .coach-msg__bubble {
  background: rgba(0, 0, 0, 0.06);
}
.coach-msg__error {
  color: #d64545;
}
.coach-msg__caliber {
  margin-top: 4px;
  display: block;
}
.coach-msg__context {
  margin: 6px 0 0;
  padding: 6px;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.04);
  font-size: 12px;
  white-space: pre-wrap;
  overflow-x: auto;
}
.coach-input-bar {
  display: flex;
  gap: 6px;
  margin-top: 8px;
}
.coach-input-bar input {
  flex: 1;
}
</style>
