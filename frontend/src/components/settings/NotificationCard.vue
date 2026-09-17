<script setup>
/* 设置-通知：逐类事件开关 + 出站 Webhook（Bark/ntfy/企业微信/通用）。
 * 事件开关改动即保存；Webhook 地址后端只回显掩码，表单未改动时提交
 * null（保持已保存地址），改动后提交字面值（空串 = 清除）。 */
import { computed, onMounted, reactive, ref } from "vue";
import { getNotifyConfig, saveNotifyConfig, testNotifyWebhook } from "../../api/notify";
import { isBusy, runTask } from "../../composables/useLoading";
import AppIcon from "../AppIcon.vue";

const events = ref([]);
const webhook = reactive({ enabled: false, type: "generic", urlInput: "", types: [] });
/* 地址输入框是否被用户改过：决定提交 null（保持）还是字面值 */
const urlDirty = ref(false);
const savedHint = ref("");
const loadFailed = ref(false);

const loading = () => isBusy("notify:load");
const saving = () => isBusy("notify:save");
const testing = () => isBusy("notify:test");

const WEBHOOK_TYPE_LABELS = {
  bark: "Bark（iOS）",
  ntfy: "ntfy",
  wecom: "企业微信机器人",
  generic: "通用 JSON 接口",
};

async function load() {
  await runTask({
    key: "notify:load",
    title: "读取通知配置",
    mode: "latest",
    rethrow: false,
    successText: "通知配置已加载",
    task: async () => {
      let data;
      try {
        data = await getNotifyConfig();
      } catch (err) {
        loadFailed.value = true;
        throw new Error("通知配置加载失败：" + err.message);
      }
      loadFailed.value = false;
      events.value = data.events || [];
      webhook.types = data.webhook?.types || Object.keys(WEBHOOK_TYPE_LABELS);
      webhook.enabled = !!data.webhook?.enabled;
      webhook.type = data.webhook?.type || "generic";
      savedHint.value = data.webhook?.url_hint || "";
      return data;
    },
  });
}

async function persist(payload, successText) {
  return runTask({
    key: "notify:save",
    title: "保存通知配置",
    detail: "正在写入配置…",
    rethrow: false,
    successText,
    task: async () => {
      const data = await saveNotifyConfig(payload);
      events.value = data.events || [];
      webhook.types = data.webhook?.types || webhook.types;
      webhook.enabled = !!data.webhook?.enabled;
      webhook.type = data.webhook?.type || "generic";
      savedHint.value = data.webhook?.url_hint || "";
      return data;
    },
  });
}

async function toggleEvent(ev, target) {
  /* 先本地翻转让开关即时反馈；保存失败时重载回滚真实状态 */
  ev.enabled = target;
  const saved = await persist(
    { events: { [ev.type]: target } },
    `「${ev.label}」已${target ? "开启" : "关闭"}`
  );
  if (!saved) await load();
}

async function saveWebhook() {
  /* 未改动的地址提交 null（保持已保存值）；改动后提交字面值（空串 = 清除） */
  const payload = {
    webhook: {
      enabled: webhook.enabled,
      type: webhook.type,
      url: urlDirty.value ? webhook.urlInput.trim() : null,
    },
  };
  const done = await persist(payload, "通知配置已保存");
  if (done) {
    urlDirty.value = false;
    webhook.urlInput = "";
  }
}

async function testWebhook() {
  await runTask({
    key: "notify:test",
    title: "发送测试推送",
    detail: "正在向 Webhook 地址发送测试通知…",
    rethrow: false,
    successText: (r) => r?.message || "已发送",
    failed: (r) => !!r && r.ok === false,
    task: async () => {
      const payload = { type: webhook.type };
      /* 表单有输入用输入值，否则空串让后端用已保存地址测试 */
      payload.url = urlDirty.value ? webhook.urlInput.trim() : "";
      return await testNotifyWebhook(payload);
    },
  });
}

const canTest = computed(
  () => urlDirty.value ? webhook.urlInput.trim() !== "" : !!savedHint.value
);

onMounted(load);
</script>

<template>
  <div class="settings-box">
    <div class="section-head">
      <h3>通知</h3>
      <span class="section-head__hint">应用内通知与手机推送</span>
    </div>
    <p class="hint">
      通知出现在右上角铃铛；配置出站 Webhook 后可同步推送到手机（Bark / ntfy / 企业微信）。
    </p>

    <div v-if="loading()" class="hint">加载中…</div>
    <template v-else-if="!loadFailed">
      <ul class="notify-events">
        <li v-for="ev in events" :key="ev.type" class="notify-event">
          <span class="notify-event__label">{{ ev.label }}</span>
          <label class="switch-row">
            <input
              type="checkbox"
              :checked="ev.enabled"
              :disabled="saving()"
              @change="toggleEvent(ev, $event.target.checked)"
            />
            <em>{{ ev.enabled ? "已开启" : "已关闭" }}</em>
          </label>
        </li>
      </ul>

      <div class="notify-webhook">
        <div class="notify-webhook__head">
          <span class="notify-webhook__title">出站 Webhook</span>
          <label class="switch-row">
            <input v-model="webhook.enabled" type="checkbox" />
            <em>{{ webhook.enabled ? "已启用" : "已关闭" }}</em>
          </label>
        </div>
        <div class="notify-webhook__form">
          <label class="field">
            <span>类型</span>
            <select v-model="webhook.type">
              <option v-for="t in webhook.types" :key="t" :value="t">
                {{ WEBHOOK_TYPE_LABELS[t] || t }}
              </option>
            </select>
          </label>
          <label class="field field--grow">
            <span>地址</span>
            <input
              v-model="webhook.urlInput"
              type="text"
              :placeholder="savedHint ? `已保存（${savedHint}），留空保持不变` : 'https://…'"
              @input="urlDirty = true"
            />
          </label>
          <div class="notify-webhook__ops">
            <button class="btn mini" :disabled="testing() || !canTest" @click="testWebhook">
              <AppIcon name="sparkles" :size="14" />
              {{ testing() ? "发送中…" : "发送测试" }}
            </button>
            <button class="btn mini" :disabled="saving()" @click="saveWebhook">
              {{ saving() ? "保存中…" : "保存 Webhook" }}
            </button>
          </div>
        </div>
        <p class="hint">
          Bark 填 https://api.day.app/你的Key；ntfy 填 https://ntfy.sh/主题；
          企业微信填机器人完整 Webhook 地址。推送失败不产生报错打扰，原因记录在运行日志中。
        </p>
      </div>
    </template>
    <div v-else class="fail">
      通知配置加载失败
      <button class="btn mini" @click="load">重试</button>
    </div>
  </div>
</template>

<style scoped>
.notify-events {
  list-style: none;
  margin: var(--space-3) 0 0;
  padding: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.notify-event {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
}
.notify-event + .notify-event {
  border-top: 1px solid var(--color-divider);
}
.notify-event__label {
  font-size: var(--text-sm);
  color: var(--color-text);
}
.notify-webhook {
  margin-top: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.notify-webhook__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.notify-webhook__title {
  font-weight: 550;
  font-size: var(--text-sm);
}
.notify-webhook__form {
  display: flex;
  align-items: flex-end;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}
.field--grow {
  flex: 1;
  min-width: 220px;
}
.notify-webhook__ops {
  display: flex;
  gap: var(--space-2);
}
.fail {
  color: var(--color-danger);
  font-size: var(--text-xs);
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
</style>
