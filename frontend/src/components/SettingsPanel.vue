<script setup>
import { onMounted, reactive, ref } from "vue";
import { api } from "../api";
import { store } from "../store";
import { toast } from "../toast";

const TYPE_LABEL = { sqlite: "SQLite（本地文件）", mysql: "MySQL", postgresql: "PostgreSQL" };
const DEFAULT_PORT = { mysql: 3306, postgresql: 5432 };

const info = ref(null);
const form = reactive({
  db_type: "mysql",
  host: "127.0.0.1",
  port: DEFAULT_PORT.mysql,
  name: "",
  user: "root",
  password: "",
});
const testing = ref(false);
const migrating = ref(false);
const testResult = ref(null);
const migrateResult = ref(null);

async function loadInfo() {
  try {
    info.value = await api("/api/settings/database");
  } catch (err) {
    toast(err.message, true);
  }
}
onMounted(loadInfo);

const claiming = ref(false);

async function claimLegacy() {
  claiming.value = true;
  try {
    const res = await api("/api/settings/user/claim", { method: "POST" });
    toast(res.claimed > 0 ? `已认领 ${res.claimed} 条历史流水` : "没有需要认领的历史流水");
    await loadInfo();
  } catch (err) {
    toast(err.message, true);
  } finally {
    claiming.value = false;
  }
}

function accountLabel() {
  if (!info.value) return "";
  if (!info.value.user_id) return "本地模式（无网关身份）";
  return info.value.user_name ? `${info.value.user_name}（UID ${info.value.user_id}）` : `UID ${info.value.user_id}`;
}

function onTypeChange() {
  form.port = DEFAULT_PORT[form.db_type];
  testResult.value = null;
}

function targetPayload() {
  return {
    db_type: form.db_type,
    host: form.host.trim(),
    port: form.port || null,
    name: form.name.trim(),
    user: form.user.trim(),
    password: form.password,
  };
}

function validate() {
  if (!form.host.trim()) {
    toast("请填写主机地址", true);
    return false;
  }
  if (!form.name.trim()) {
    toast("请填写数据库名", true);
    return false;
  }
  return true;
}

async function testConnection() {
  if (!validate()) return;
  testing.value = true;
  testResult.value = null;
  try {
    testResult.value = await api("/api/settings/database/test", {
      method: "POST",
      body: JSON.stringify(targetPayload()),
    });
  } catch (err) {
    testResult.value = { ok: false, message: err.message };
  } finally {
    testing.value = false;
  }
}

async function migrate() {
  if (!validate()) return;
  const target = `${form.db_type.toUpperCase()}（${form.host}:${form.port}/${form.name}）`;
  const msg =
    `确定把当前数据库的 ${info.value?.bills ?? 0} 条流水、${info.value?.categories ?? 0} 个分类` +
    `迁移到 ${target} 吗？\n\n` +
    "迁移成功后将立即切换到新数据库；原数据库数据保留不动，可随时改回。";
  if (!confirm(msg)) return;
  migrating.value = true;
  migrateResult.value = null;
  try {
    migrateResult.value = await api("/api/settings/database/migrate", {
      method: "POST",
      body: JSON.stringify(targetPayload()),
    });
    toast("迁移完成，已切换到新数据库");
    await loadInfo();
  } catch (err) {
    migrateResult.value = { ok: false, message: err.message };
    toast(err.message, true);
  } finally {
    migrating.value = false;
  }
}
</script>

<template>
  <section class="panel" :class="{ active: store.tab === 'settings' }">
    <div class="settings-box">
      <h3>当前数据库</h3>
      <template v-if="info">
        <ul class="db-meta">
          <li>
            <span class="k">当前账号</span>
            <span class="v"><span class="db-type-badge">{{ accountLabel() }}</span></span>
          </li>
          <li>
            <span class="k">数据库类型</span>
            <span class="v">{{ TYPE_LABEL[info.db_type] || info.db_type }}</span>
          </li>
          <li v-if="info.sqlite_path">
            <span class="k">数据文件</span>
            <span class="v">{{ info.sqlite_path }}</span>
          </li>
          <li v-else>
            <span class="k">连接地址</span>
            <span class="v">{{ info.host }}:{{ info.port }}</span>
          </li>
          <li v-if="!info.sqlite_path">
            <span class="k">数据库名</span>
            <span class="v">{{ info.name }}</span>
          </li>
          <li v-if="!info.sqlite_path">
            <span class="k">用户名</span>
            <span class="v">{{ info.user }}</span>
          </li>
          <li>
            <span class="k">流水 / 分类</span>
            <span class="v">{{ info.bills }} 条 / {{ info.categories }} 个</span>
          </li>
          <li>
            <span class="k">表结构版本</span>
            <span class="v">v{{ info.schema_version }}（最新 v{{ info.schema_latest }}）</span>
          </li>
        </ul>
        <div v-if="info.user_id && info.unassigned_bills > 0" class="claim-box">
          <span>检测到 {{ info.unassigned_bills }} 条升级前的历史流水（无账号归属）。</span>
          <button class="btn primary" :disabled="claiming" @click="claimLegacy">
            {{ claiming ? "认领中…" : "认领到当前账号" }}
          </button>
        </div>
      </template>
      <p v-else class="hint">加载中…</p>
    </div>

    <div class="settings-box">
      <h3>迁移到新数据库</h3>
      <p class="hint">
        把当前数据库中的全部流水与分类搬到新的 MySQL / PostgreSQL 数据库，迁移成功后立即切换。
        原数据库只读保留不动，改回配置即可回退；目标库已有数据时按交易单号去重合并。
      </p>
      <div class="form-grid">
        <label>
          数据库类型
          <select v-model="form.db_type" @change="onTypeChange">
            <option value="mysql">MySQL</option>
            <option value="postgresql">PostgreSQL</option>
          </select>
        </label>
        <label>
          主机地址
          <input v-model="form.host" type="text" placeholder="如 192.168.1.10" />
        </label>
        <label>
          端口
          <input v-model.number="form.port" type="number" :placeholder="String(DEFAULT_PORT[form.db_type])" />
        </label>
        <label>
          数据库名
          <input v-model="form.name" type="text" placeholder="如 fn_finstat" />
        </label>
        <label>
          用户名
          <input v-model="form.user" type="text" placeholder="root" />
        </label>
        <label>
          密码
          <input v-model="form.password" type="password" placeholder="数据库密码" />
        </label>
      </div>
      <div class="settings-actions">
        <button class="btn" :disabled="testing || migrating" @click="testConnection">
          {{ testing ? "测试中…" : "测试连接" }}
        </button>
        <button class="btn primary" :disabled="testing || migrating" @click="migrate">
          {{ migrating ? "迁移中，请稍候…" : "迁移并切换" }}
        </button>
      </div>

      <div class="settings-result" :class="{ show: !!testResult, ok: testResult?.ok, err: testResult && !testResult.ok }">
        <template v-if="testResult">
          <template v-if="testResult.ok">
            <b>连接成功</b>（{{ testResult.server_version }}）
            <span v-if="testResult.target_empty === true">· 目标库为空，将全量搬移并保留流水编号</span>
            <span v-else-if="testResult.target_empty === false">· 目标库已有数据，将按交易单号去重合并</span>
            <span v-else>· 目标库尚无业务表，迁移时自动创建</span>
          </template>
          <template v-else>连接失败：{{ testResult.message }}</template>
        </template>
      </div>

      <div class="settings-result" :class="{ show: !!migrateResult, ok: migrateResult?.ok, err: migrateResult && !migrateResult.ok }">
        <template v-if="migrateResult">
          <template v-if="migrateResult.ok">
            <b>迁移完成</b>：复制 {{ migrateResult.copied_bills }} 条流水、
            {{ migrateResult.copied_categories }} 个分类
            <span v-if="migrateResult.merged">（目标原有数据，已去重合并）</span>，
            已切换到新数据库。原数据库数据保留于原处，可随时改回。
          </template>
          <template v-else>迁移失败：{{ migrateResult.message }}</template>
        </template>
      </div>
    </div>
  </section>
</template>
