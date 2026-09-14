<script setup>
/* 设置-数据库：当前数据库概览、历史流水认领、迁移到新数据库并切换。
 * 两块共用同一份 info 状态（迁移完成后需刷新计数），故合并在一个组件内。 */
import { computed, onMounted, reactive, ref } from "vue";
import { databaseInfo, claimLegacyBills, migrateDatabase, testTargetDatabase } from "../../api/settings";
import { confirm } from "../../composables/useConfirm";
import { isBusy, runTask } from "../../composables/useLoading";
import { toast } from "../../toast";

const TYPE_LABEL = { sqlite: "SQLite（本地文件）", mysql: "MySQL", postgresql: "PostgreSQL" };
const DEFAULT_PORT = { mysql: 3306, postgresql: 5432 };

/* 可展开的说明块集合 */
const openNotes = ref({});
function toggleNote(key) {
  openNotes.value[key] = !openNotes.value[key];
}

const info = ref(null);
const form = reactive({
  db_type: "mysql",
  host: "127.0.0.1",
  port: DEFAULT_PORT.mysql,
  name: "",
  user: "root",
  password: "",
});
/* 忙标记统一由 loading 层的 key 锁派生 */
const testing = computed(() => isBusy("db:test"));
const migrating = computed(() => isBusy("db:migrate"));
const claiming = computed(() => isBusy("db:claim"));
const testResult = ref(null);
const migrateResult = ref(null);
/* 信息加载失败：显示失败态与重试入口，而不是永远停留「加载中」占位 */
const infoLoadFailed = ref(false);

async function loadInfo() {
  await runTask({
    key: "db:info",
    title: "读取数据库信息",
    detail: "正在查询当前连接与数据量…",
    mode: "latest",
    rethrow: false,
    successText: "数据库信息已更新",
    task: async () => {
      try {
        info.value = await databaseInfo();
      } catch (err) {
        infoLoadFailed.value = true;
        throw err;
      }
      infoLoadFailed.value = false;
      return info.value;
    },
  });
}
onMounted(loadInfo);

/* 供父级（BackupCard 恢复成功后）刷新数据库计数 */
defineExpose({ loadInfo });

async function claimLegacy() {
  await runTask({
    key: "db:claim",
    title: "认领历史流水",
    detail: "正在把无主流水挂到当前账号…",
    rethrow: false,
    successText: (res) =>
      res && res.claimed > 0 ? `已认领 ${res.claimed} 条历史流水` : "没有需要认领的历史流水",
    task: async () => {
      const res = await claimLegacyBills();
      await loadInfo();
      return res;
    },
  });
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
  testResult.value = null;
  try {
    testResult.value = await runTask({
      key: "db:test",
      title: "测试数据库连接",
      detail: `正在连接 ${form.host}:${form.port}…`,
      successText: (r) => (r && r.ok ? "连接成功" : (r && r.message) || "连接失败"),
      /* 业务失败（HTTP 200 + ok:false）走错误相位：避免浮层绿勾配「连接失败」文案 */
      failed: (r) => !!r && r.ok === false,
      task: () => testTargetDatabase(targetPayload()),
    });
  } catch (err) {
    /* 失败原因已在进度浮层展示，这里同步写回卡片内的结果区 */
    testResult.value = { ok: false, message: err.message };
  }
}

async function migrate() {
  if (!validate()) return;
  const target = `${form.db_type.toUpperCase()}（${form.host}:${form.port}/${form.name}）`;
  const msg =
    `确定把当前数据库的 ${info.value?.bills ?? 0} 条流水、${info.value?.categories ?? 0} 个分类` +
    `迁移到 ${target} 吗？\n\n` +
    "迁移成功后将立即切换到新数据库；原数据库数据保留不动，可随时改回。";
  const okToMigrate = await confirm({ title: "迁移数据库", message: msg, confirmText: "开始迁移" });
  if (!okToMigrate) return;
  migrateResult.value = null;
  try {
    migrateResult.value = await runTask({
      key: "db:migrate",
      title: "迁移数据库",
      detail: `正在迁移到 ${form.host}:${form.port}/${form.name}…`,
      successText: "迁移完成，已切换到新数据库",
      task: async (update) => {
        const res = await migrateDatabase(targetPayload());
        form.password = ""; // 已切换到新库，密码不驻留表单（失败时保留以便重试）
        update("迁移完成，正在刷新数据库信息…");
        await loadInfo();
        return res;
      },
    });
  } catch (err) {
    migrateResult.value = { ok: false, message: err.message };
  }
}
</script>

<template>
  <!-- 当前数据库 -->
  <div class="settings-box">
    <div class="section-head">
      <h3>当前数据库</h3>
      <span class="section-head__hint">数据隔离按飞牛登录账号生效，分类为全账号共享</span>
    </div>
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
    <div v-else-if="infoLoadFailed" class="settings-result show err">
      数据库信息加载失败
      <button class="btn mini" :disabled="isBusy('db:info')" @click="loadInfo">重试</button>
    </div>
    <p v-else class="hint">加载中…</p>
  </div>

  <!-- 迁移数据库 -->
  <div class="settings-box">
    <div class="section-head">
      <h3>迁移到新数据库</h3>
      <button class="note-toggle" :aria-expanded="!!openNotes.migrate" @click="toggleNote('migrate')">
        {{ openNotes.migrate ? "收起说明" : "迁移说明" }}
      </button>
    </div>
    <p class="hint">把全部流水与分类搬到新的 MySQL / PostgreSQL，并立即切换，无需重启应用。</p>
    <div v-show="openNotes.migrate" class="note-box">
      迁移成功后立即切换到新数据库；原数据库只读保留不动，改回配置即可回退。
      目标库为空时整库搬移并保留流水编号；目标库已有数据时按交易单号去重合并。
      目标库驱动缺失时会自动安装，无需重装应用。
    </div>
    <div class="form-grid">
      <label class="field">
        数据库类型
        <select v-model="form.db_type" @change="onTypeChange">
          <option value="mysql">MySQL</option>
          <option value="postgresql">PostgreSQL</option>
        </select>
      </label>
      <label class="field">
        主机地址
        <input v-model="form.host" type="text" placeholder="如 192.168.1.10" />
      </label>
      <label class="field">
        端口
        <input v-model.number="form.port" type="number" :placeholder="String(DEFAULT_PORT[form.db_type])" />
      </label>
      <label class="field">
        数据库名
        <input v-model="form.name" type="text" placeholder="如 fn_finstat" />
      </label>
      <label class="field">
        用户名
        <input v-model="form.user" type="text" placeholder="root" />
      </label>
      <label class="field">
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
</template>
