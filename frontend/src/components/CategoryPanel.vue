<script setup>
/* 分类管理（v1.1 树形版）：
 * - 分类两级层级展示（树接口 rollup 含子孙的流水数与支出）
 * - 每行嵌关键词抽屉：增 / 停用 / 删除（内置词同样可停用，来源徽标区分）
 * - AI 两段式：「AI 补关键词」「AI 生成子类」→ 候选勾选 → 确认落库
 *   （生成产生 API 费用、可重复应用不重复计费；未配 Key 时按钮禁用） */
import { computed, onMounted, reactive, ref } from "vue";
import {
  addKeywords,
  createCategory,
  deleteCategory,
  deleteKeyword,
  getCategory,
  getCategoryTree,
  listKeywords,
  renameCategory,
  toggleKeyword,
} from "../api/category";
import {
  aiApplyCategoryChildren,
  aiApplyCategoryKeywords,
  aiGenerateCategoryChildren,
  aiGenerateCategoryKeywords,
  aiConfig,
} from "../api/ai";
import { confirm } from "../composables/useConfirm";
import { isBusy, runTask, TASK_CANCELLED } from "../composables/useLoading";
import { refreshCategories, store } from "../store";
import { fmtMoney } from "../utils/format";
import { toast } from "../toast";
import AppIcon from "./AppIcon.vue";

const newName = ref("");
const editingId = ref(null);
const editName = ref("");

/* 分类操作期间整体锁住，避免连点重复建同名分类 */
const busy = computed(
  () =>
    isBusy("category:add") ||
    isBusy("category:rename") ||
    isBusy("category:delete")
);

const tree = ref([]);
const expanded = ref(new Set());
const kwMap = reactive({}); // 分类 id -> 关键词列表（展开时懒加载）
const kwDraft = reactive({}); // 分类 id -> 抽屉里的批量输入
const aiReady = ref(false);

const totalCats = computed(() => {
  const count = (nodes) => nodes.reduce((n, r) => n + 1 + count(r.children || []), 0);
  return count(tree.value);
});

function isOpen(id) {
  return expanded.value.has(id);
}

async function loadTree() {
  await runTask({
    key: "category:tree",
    title: "加载分类",
    detail: "正在读取分类树…",
    rethrow: false,
    task: async () => {
      tree.value = await getCategoryTree();
    },
  });
}

async function loadKeywords(cat) {
  if (kwMap[cat.id]) return;
  const rows = await listKeywords(cat.id);
  kwMap[cat.id] = rows;
}

function toggle(cat) {
  const next = new Set(expanded.value);
  if (next.has(cat.id)) {
    next.delete(cat.id);
  } else {
    next.add(cat.id);
    loadKeywords(cat).catch(() => {});
    for (const child of cat.children || []) loadKeywords(child).catch(() => {});
  }
  expanded.value = next;
}

const KEYWORD_SOURCE_LABEL = { builtin: "内置", ai: "AI", manual: "手动" };

async function addKeywordBatch(cat) {
  const raw = (kwDraft[cat.id] || "").trim();
  if (!raw) {
    toast("请输入关键词（可用空格或逗号分隔多个）", true);
    return;
  }
  const words = raw.split(/[,，、\s]+/).filter(Boolean);
  await runTask({
    key: "kw:add",
    title: "新增关键词",
    detail: `正在为「${cat.name}」写入关键词…`,
    rethrow: false,
    successText: (res) => {
      if (!res) return "已完成";
      const parts = [`新增 ${res.added} 个`];
      if (res.duplicated) parts.push(`重复跳过 ${res.duplicated} 个`);
      if (res.dropped) parts.push(`非法剔除 ${res.dropped} 个`);
      return `关键词${parts.join("，")}`;
    },
    task: async () => {
      const res = await addKeywords(cat.id, words);
      kwDraft[cat.id] = "";
      kwMap[cat.id] = await listKeywords(cat.id);
      return res;
    },
  });
}

async function flipKeyword(cat, kw) {
  await runTask({
    key: "kw:toggle",
    title: "更新关键词",
    rethrow: false,
    task: async () => {
      await toggleKeyword(kw.id, !kw.enabled);
      kwMap[cat.id] = await listKeywords(cat.id);
    },
  });
}

async function removeKeyword(cat, kw) {
  await runTask({
    key: "kw:del",
    title: "删除关键词",
    rethrow: false,
    task: async () => {
      await deleteKeyword(kw.id);
      kwMap[cat.id] = await listKeywords(cat.id);
    },
  });
}

/* ---- AI 两段式弹窗 ---- */

const aiModal = reactive({
  show: false,
  phase: "input", // input（未生成）| ready（候选已出）
  type: "keywords", // keywords | children
  category: null,
  hint: "",
  result: null,
  selected: new Set(), // 勾选的关键词 / 子类名
  migrateBills: false,
});
const aiBusy = computed(
  () =>
    isBusy("cat:ai-gen") ||
    isBusy("cat:ai-apply") ||
    isBusy("kw:add") ||
    isBusy("kw:toggle") ||
    isBusy("kw:del")
);

function openKeywordAI(cat) {
  if (!aiReady.value) {
    toast("请先在设置页配置 AI API Key", true);
    return;
  }
  Object.assign(aiModal, {
    show: true,
    phase: "input",
    type: "keywords",
    category: cat,
    hint: "",
    result: null,
    selected: new Set(),
    migrateBills: false,
  });
}

function openChildrenAI(cat) {
  if (!aiReady.value) {
    toast("请先在设置页配置 AI API Key", true);
    return;
  }
  Object.assign(aiModal, {
    show: true,
    phase: "input",
    type: "children",
    category: cat,
    hint: "",
    result: null,
    selected: new Set(),
    migrateBills: false,
  });
}

function closeAiModal() {
  if (aiBusy.value) return;
  aiModal.show = false;
}

async function generate() {
  const cat = aiModal.category;
  const isKeywords = aiModal.type === "keywords";
  await runTask({
    key: "cat:ai-gen",
    title: isKeywords ? "AI 生成关键词" : "AI 生成子类方案",
    detail: `正在分析「${cat.name}」的商户样本…（需数秒，产生 API 费用）`,
    rethrow: false,
    task: async () => {
      const res = isKeywords
        ? await aiGenerateCategoryKeywords(cat.id, aiModal.hint)
        : await aiGenerateCategoryChildren(cat.id);
      aiModal.result = res;
      aiModal.phase = "ready";
      aiModal.selected = new Set(
        isKeywords
          ? res.candidates.filter((c) => !c.conflict).map((c) => c.keyword)
          : res.children.map((c) => c.name)
      );
      return res;
    },
  });
}

async function applyAI() {
  const cat = aiModal.category;
  const isKeywords = aiModal.type === "keywords";
  await runTask({
    key: "cat:ai-apply",
    title: isKeywords ? "写入关键词" : "应用子类方案",
    detail: "正在保存…",
    rethrow: false,
    successText: (res) => {
      if (!isKeywords && res && res.created?.length) {
        const migrated = res.migrated ? `，迁移 ${res.migrated} 条流水` : "";
        return `已创建 ${res.created.map((c) => c.name).join("、")}${migrated}`;
      }
      return `已写入 ${res?.added ?? 0} 个关键词`;
    },
    task: async (update) => {
      let res;
      if (isKeywords) {
        const words = aiModal.result.candidates
          .filter((c) => aiModal.selected.has(c.keyword))
          .map((c) => c.keyword);
        if (!words.length) {
          toast("请至少勾选一个关键词", true);
          return TASK_CANCELLED;
        }
        update(`正在写入 ${words.length} 个关键词…`);
        res = await aiApplyCategoryKeywords(cat.id, words);
      } else {
        const children = aiModal.result.children.filter((c) =>
          aiModal.selected.has(c.name)
        );
        if (!children.length) {
          toast("请至少勾选一个子类", true);
          return TASK_CANCELLED;
        }
        update(aiModal.migrateBills ? "正在建子类并迁移流水…" : "正在建子类…");
        res = await aiApplyCategoryChildren(
          cat.id,
          children,
          aiModal.migrateBills
        );
      }
      aiModal.show = false;
      expanded.value = new Set([...expanded.value, cat.id]);
      delete kwMap[cat.id];
      await loadKeywords(cat).catch(() => {});
      await Promise.all([loadTree(), refreshCategories().catch(() => {})]);
      return res;
    },
  });
}

/* ---- 分类增删改（既有能力，适配树数据） ---- */

async function add() {
  const name = newName.value.trim();
  if (!name) {
    toast("请输入分类名称", true);
    return;
  }
  await runTask({
    key: "category:add",
    title: "新增分类",
    detail: `正在创建「${name}」…`,
    rethrow: false,
    successText: "分类已新增",
    task: async () => {
      await createCategory(name);
      newName.value = "";
      await Promise.all([loadTree(), refreshCategories().catch(() => {})]);
    },
  });
}

function startEdit(c) {
  editingId.value = c.id;
  editName.value = c.name;
}

function cancelEdit() {
  editingId.value = null;
  editName.value = "";
}

async function saveEdit() {
  const name = editName.value.trim();
  if (!name) {
    toast("分类名称不能为空", true);
    return;
  }
  await runTask({
    key: "category:rename",
    title: "重命名分类",
    detail: `正在更新为「${name}」…`,
    rethrow: false,
    successText: (res) =>
      res && res.renamed_bills > 0
        ? `已重命名，${res.renamed_bills} 条流水同步更新`
        : "分类已更新",
    task: async () => {
      const res = await renameCategory(editingId.value, name);
      editingId.value = null;
      await Promise.all([loadTree(), refreshCategories().catch(() => {})]);
      return res;
    },
  });
}

async function remove(c) {
  await runTask({
    key: "category:delete",
    title: "删除分类",
    detail: `正在检查「${c.name}」…`,
    rethrow: false,
    successText: (res) =>
      res && res.moved_bills > 0
        ? `已删除，${res.moved_bills} 条流水归入「其他」`
        : "已删除",
    task: async (update) => {
      /* 先取详情拿流水数，删除前给出明确告知；有子分类时后端也会拒绝 */
      const detail = await getCategory(c.id);
      const childTip =
        (c.children || []).length > 0
          ? `（含 ${c.children.length} 个子分类，需先删除子分类）`
          : "";
      const msg = detail.bill_count > 0
        ? `「${c.name}」下有 ${detail.bill_count} 条流水，删除后将归入「其他」${childTip}，确定删除吗？`
        : `确定删除分类「${c.name}」吗？${childTip}`;
      const okToDelete = await confirm({
        title: "删除分类",
        message: msg,
        danger: true,
        confirmText: "删除",
      });
      if (!okToDelete) return TASK_CANCELLED;
      update(`正在删除「${c.name}」…`);
      const res = await deleteCategory(c.id);
      expanded.value = new Set([...expanded.value].filter((id) => id !== c.id));
      delete kwMap[c.id];
      await Promise.all([loadTree(), refreshCategories().catch(() => {})]);
      return res;
    },
  });
}

onMounted(async () => {
  loadTree();
  try {
    const cfg = await aiConfig();
    aiReady.value = !!cfg?.has_api_key;
  } catch {
    aiReady.value = false;
  }
});
</script>

<template>
  <section class="panel" :class="{ active: store.tab === 'categories' }">
    <div class="chart-box">
      <div class="section-head">
        <h3>新增分类</h3>
        <span class="section-head__hint">
          分类为全账号共享；关键词自动归类（可在下方维护），未命中的流水归入「其他」
        </span>
      </div>
      <div class="add-row">
        <input
          v-model="newName"
          type="text"
          placeholder="输入新分类名称，如：数码"
          maxlength="20"
          aria-label="新分类名称"
          @keydown.enter="add"
        />
        <button class="btn primary" :disabled="busy" @click="add">
          <AppIcon name="plus" :size="15" /> 新增
        </button>
      </div>
    </div>

    <div class="chart-box">
      <div class="section-head">
        <h3>全部分类</h3>
        <span class="section-head__hint">
          共 {{ totalCats }} 个 · 改名会同步更新已归类流水 · 点击行展开子分类与关键词
        </span>
      </div>
      <ul class="category-list">
        <li v-if="!tree.length" class="empty" style="background: none">暂无分类</li>
        <li v-for="root in tree" :key="root.id" class="cat-node">
          <template v-if="editingId === root.id">
            <div class="cat-row">
              <input
                v-model="editName"
                class="cat-edit"
                type="text"
                maxlength="20"
                aria-label="分类名称"
                @keydown.enter="saveEdit"
                @keydown.esc="cancelEdit"
              />
              <span class="cat-actions">
                <button class="btn mini" :disabled="busy" @click="saveEdit">保存</button>
                <button class="btn mini" @click="cancelEdit">取消</button>
              </span>
            </div>
          </template>
          <template v-else>
            <div class="cat-row clickable" @click="toggle(root)">
              <button
                class="cat-expand"
                :class="{ flip: isOpen(root.id) }"
                :aria-expanded="isOpen(root.id)"
                :aria-label="`展开或收起 ${root.name}`"
                @click.stop="toggle(root)"
              >
                <AppIcon name="chevronDown" :size="14" />
              </button>
              <span class="cat-name">{{ root.name }}</span>
              <span v-if="root.source === 'ai'" class="cat-badge ai" title="AI 自动创建">AI</span>
              <span v-if="root.name === '其他'" class="cat-badge" title="自动归类的兜底分类，不可编辑">内置</span>
              <span class="cat-meta">
                {{ root.bill_count }} 笔 · {{ fmtMoney(root.expense_total) }}
              </span>
              <span class="cat-actions" @click.stop>
                <button class="btn mini" title="维护关键词" @click="toggle(root)">关键词</button>
                <button class="btn mini" :disabled="aiBusy" title="AI 总结该分类的高频关键词" @click="openKeywordAI(root)">
                  <AppIcon name="sparkles" :size="12" /> AI 补词
                </button>
                <button class="btn mini" :disabled="aiBusy" title="AI 依据商户分布建议子分类" @click="openChildrenAI(root)">
                  <AppIcon name="sparkles" :size="12" /> AI 子类
                </button>
                <button class="btn mini" @click="startEdit(root)">编辑</button>
                <button class="btn mini danger" :disabled="busy" @click="remove(root)">删除</button>
              </span>
            </div>

            <div v-if="isOpen(root.id)" class="kw-drawer">
              <div class="kw-head">关键词（命中即归类为「{{ root.name }}」，长词优先）</div>
              <div class="kw-chips">
                <span
                  v-for="kw in kwMap[root.id] || []"
                  :key="kw.id"
                  class="chip kw-chip"
                  :class="{ off: !kw.enabled }"
                  :title="kw.enabled ? '点击停用' : '已停用，点击启用'"
                >
                  <button class="kw-word" @click="flipKeyword(root, kw)">{{ kw.keyword }}</button>
                  <span v-if="kw.source !== 'manual'" class="kw-src">{{ KEYWORD_SOURCE_LABEL[kw.source] || kw.source }}</span>
                  <button class="kw-del" aria-label="删除关键词" @click="removeKeyword(root, kw)">×</button>
                </span>
                <span v-if="!(kwMap[root.id] || []).length" class="kw-empty">暂无关键词</span>
              </div>
              <div class="kw-add">
                <input
                  v-model="kwDraft[root.id]"
                  type="text"
                  placeholder="新增关键词，空格或逗号分隔可批量"
                  @keydown.enter="addKeywordBatch(root)"
                />
                <button class="btn mini" :disabled="aiBusy" @click="addKeywordBatch(root)">添加</button>
              </div>
            </div>

            <ul v-if="isOpen(root.id) && (root.children || []).length" class="cat-children">
              <li v-for="child in root.children" :key="child.id" class="cat-node child">
                <template v-if="editingId === child.id">
                  <div class="cat-row">
                    <input
                      v-model="editName"
                      class="cat-edit"
                      type="text"
                      maxlength="20"
                      @keydown.enter="saveEdit"
                      @keydown.esc="cancelEdit"
                    />
                    <span class="cat-actions">
                      <button class="btn mini" :disabled="busy" @click="saveEdit">保存</button>
                      <button class="btn mini" @click="cancelEdit">取消</button>
                    </span>
                  </div>
                </template>
                <template v-else>
                  <div class="cat-row">
                    <span class="cat-branch">└</span>
                    <span class="cat-name">{{ child.name }}</span>
                    <span v-if="child.source === 'ai'" class="cat-badge ai" title="AI 创建的子分类">AI</span>
                    <span class="cat-meta">{{ child.bill_count }} 笔 · {{ fmtMoney(child.expense_total) }}</span>
                    <span class="cat-actions">
                      <button class="btn mini" @click="startEdit(child)">编辑</button>
                      <button class="btn mini danger" :disabled="busy" @click="remove(child)">删除</button>
                    </span>
                  </div>
                  <div class="kw-drawer slim">
                    <div class="kw-chips">
                      <span
                        v-for="kw in kwMap[child.id] || []"
                        :key="kw.id"
                        class="chip kw-chip"
                        :class="{ off: !kw.enabled }"
                      >
                        <button class="kw-word" @click="flipKeyword(child, kw)">{{ kw.keyword }}</button>
                        <button class="kw-del" aria-label="删除关键词" @click="removeKeyword(child, kw)">×</button>
                      </span>
                      <span v-if="!(kwMap[child.id] || []).length" class="kw-empty">暂无关键词</span>
                    </div>
                    <div class="kw-add">
                      <input
                        v-model="kwDraft[child.id]"
                        type="text"
                        placeholder="新增关键词"
                        @keydown.enter="addKeywordBatch(child)"
                      />
                      <button class="btn mini" :disabled="aiBusy" @click="addKeywordBatch(child)">添加</button>
                    </div>
                  </div>
                </template>
              </li>
            </ul>
          </template>
        </li>
      </ul>
    </div>

    <!-- AI 候选勾选弹窗（两段式第二步：预览 → 勾选 → 落库） -->
    <div class="modal-mask" :class="{ show: aiModal.show }" @click.self="closeAiModal">
      <div class="modal" role="dialog" aria-modal="true" aria-label="AI 分类扩展">
        <template v-if="aiModal.type === 'keywords'">
          <h3>AI 补全关键词 ·「{{ aiModal.category?.name }}」</h3>
          <template v-if="aiModal.phase === 'input'">
            <p class="hint">
              将把该分类的高频商户样本（仅商户名，不含金额/日期/备注）交给 AI 总结关键词候选。
            </p>
            <input
              v-model="aiModal.hint"
              type="text"
              maxlength="200"
              placeholder="补充说明（可选），如：侧重外卖平台"
            />
          </template>
          <template v-else>
            <p class="hint">
              共 {{ aiModal.result?.candidates?.length || 0 }} 个候选（已生成，勾选后写入不重复计费）；
              <span class="kw-conflict">橙色</span> 表示该词已属于其它分类（同词跨分类合法，请人工判断）。
            </p>
            <div class="ai-candidates">
              <label
                v-for="cand in aiModal.result?.candidates || []"
                :key="cand.keyword"
                class="ai-cand"
                :class="{ conflict: cand.conflict }"
              >
                <input v-model="aiModal.selected" type="checkbox" :value="cand.keyword" />
                <span class="ai-cand__word">{{ cand.keyword }}</span>
                <span v-if="cand.conflict" class="ai-cand__conflict">已属「{{ cand.conflict }}」</span>
              </label>
              <p v-if="!(aiModal.result?.candidates || []).length" class="hint">没有可用的候选词，可补充说明后重新生成</p>
            </div>
          </template>
          <div class="modal-actions">
            <button class="btn" :disabled="aiBusy" @click="closeAiModal">取消</button>
            <button v-if="aiModal.phase === 'input'" class="btn primary" :disabled="aiBusy" @click="generate">
              {{ aiBusy ? "生成中…" : "生成候选（产生 API 费用）" }}
            </button>
            <button v-else class="btn primary" :disabled="aiBusy || !aiModal.selected.size" @click="applyAI">
              写入所选（{{ aiModal.selected.size }}）
            </button>
          </div>
        </template>

        <template v-else>
          <h3>AI 生成子类 ·「{{ aiModal.category?.name }}」</h3>
          <template v-if="aiModal.phase === 'input'">
            <p class="hint">
              将把该分类的商户分布（仅商户名与条数）交给 AI 设计子分类方案（含每个子类的关键词）。
            </p>
          </template>
          <template v-else>
            <p class="hint">
              {{ aiModal.result?.bill_count }} 笔流水 · {{ aiModal.result?.children?.length || 0 }} 个子类建议；取消勾选即不创建。
            </p>
            <div class="ai-candidates">
              <div v-for="child in aiModal.result?.children || []" :key="child.name" class="ai-child">
                <label class="ai-cand">
                  <input v-model="aiModal.selected" type="checkbox" :value="child.name" />
                  <span class="ai-cand__word">{{ child.name }}</span>
                  <span class="ai-cand__conflict ok">{{ child.keywords.length }} 个关键词</span>
                </label>
                <p v-if="child.reason" class="ai-child__reason">{{ child.reason }}</p>
                <p class="ai-child__keywords">{{ child.keywords.join("、") }}</p>
              </div>
              <p v-if="!(aiModal.result?.children || []).length" class="hint">
                AI 认为当前样本不足以支撑有意义的细分，未给出方案
              </p>
            </div>
            <label class="switch-row migrate-row">
              <input v-model="aiModal.migrateBills" type="checkbox" />
              <em>同时把「{{ aiModal.category?.name }}」下命中子类关键词的流水迁移到对应子类</em>
            </label>
          </template>
          <div class="modal-actions">
            <button class="btn" :disabled="aiBusy" @click="closeAiModal">取消</button>
            <button v-if="aiModal.phase === 'input'" class="btn primary" :disabled="aiBusy" @click="generate">
              {{ aiBusy ? "生成中…" : "生成方案（产生 API 费用）" }}
            </button>
            <button v-else class="btn primary" :disabled="aiBusy || !aiModal.selected.size" @click="applyAI">
              创建所选（{{ aiModal.selected.size }}）
            </button>
          </div>
        </template>
      </div>
    </div>
  </section>
</template>

<style scoped>
.category-list {
  box-shadow: none;
  border: none;
  padding: 0;
}
.cat-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-1);
  border-radius: var(--radius-md, 8px);
}
.cat-row.clickable {
  cursor: pointer;
}
.cat-row.clickable:hover {
  background: var(--color-hover, rgba(0, 0, 0, 0.04));
}
.cat-expand {
  border: none;
  background: none;
  color: var(--color-text-tertiary);
  cursor: pointer;
  display: inline-flex;
  padding: 2px;
  transition: transform var(--dur-fast, 0.15s) var(--ease-out, ease-out);
}
.cat-expand.flip {
  transform: rotate(180deg);
}
.cat-branch {
  color: var(--color-text-tertiary);
  font-size: var(--text-xs);
  min-width: 16px;
}
.cat-name {
  font-weight: 600;
  white-space: nowrap;
}
.cat-badge {
  flex-shrink: 0;
  font-size: var(--text-xs, 12px);
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--color-hover, rgba(0, 0, 0, 0.06));
  color: var(--color-text-tertiary);
}
.cat-badge.ai {
  background: var(--color-primary-soft, rgba(59, 130, 246, 0.12));
  color: var(--color-primary, #3b82f6);
}
.cat-meta {
  margin-left: auto;
  color: var(--color-text-tertiary);
  font-size: var(--text-xs, 12px);
  white-space: nowrap;
}
.cat-actions {
  display: inline-flex;
  gap: var(--space-1);
  flex-shrink: 0;
}
.cat-children {
  list-style: none;
  margin: 0;
  padding: 0 0 0 var(--space-5, 20px);
}
.cat-node.child {
  border-top: 1px dashed var(--color-border, rgba(0, 0, 0, 0.08));
}
.kw-drawer {
  margin: 0 0 var(--space-2) var(--space-6, 24px);
  padding: var(--space-2);
  border: 1px dashed var(--color-border, rgba(0, 0, 0, 0.12));
  border-radius: var(--radius-md, 8px);
}
.kw-drawer.slim {
  margin-left: var(--space-6, 24px);
}
.kw-head {
  font-size: var(--text-xs, 12px);
  color: var(--color-text-tertiary);
  margin-bottom: var(--space-1);
}
.kw-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
}
.kw-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 4px 2px 8px;
}
.kw-chip.off .kw-word {
  text-decoration: line-through;
  opacity: 0.55;
}
.kw-word {
  border: none;
  background: none;
  cursor: pointer;
  font-size: var(--text-xs, 12px);
  color: inherit;
  padding: 0;
}
.kw-src {
  font-size: 10px;
  color: var(--color-text-tertiary);
}
.kw-del {
  border: none;
  background: none;
  cursor: pointer;
  line-height: 1;
  padding: 0 2px;
  color: var(--color-text-tertiary);
}
.kw-del:hover {
  color: var(--color-danger, #dc2626);
}
.kw-empty {
  font-size: var(--text-xs, 12px);
  color: var(--color-text-tertiary);
}
.kw-add {
  display: flex;
  gap: var(--space-1);
  margin-top: var(--space-2);
}
.kw-add input {
  flex: 1;
  font-size: var(--text-xs, 12px);
}
.kw-conflict {
  color: var(--color-warning, #b45309);
  font-weight: 600;
}
.ai-candidates {
  max-height: 320px;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: var(--space-2) 0;
}
.ai-cand {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm, 6px);
  cursor: pointer;
  font-size: var(--text-sm, 13px);
}
.ai-cand:hover {
  background: var(--color-hover, rgba(0, 0, 0, 0.04));
}
.ai-cand.conflict {
  color: var(--color-warning, #b45309);
}
.ai-cand__word {
  font-weight: 600;
}
.ai-cand__conflict {
  margin-left: auto;
  font-size: var(--text-xs, 12px);
  color: var(--color-warning, #b45309);
}
.ai-cand__conflict.ok {
  color: var(--color-text-tertiary);
}
.ai-child {
  border: 1px dashed var(--color-border, rgba(0, 0, 0, 0.12));
  border-radius: var(--radius-md, 8px);
  padding: var(--space-1) var(--space-2);
}
.ai-child__reason {
  margin: 2px 0;
  font-size: var(--text-xs, 12px);
  color: var(--color-text-tertiary);
}
.ai-child__keywords {
  margin: 2px 0;
  font-size: var(--text-xs, 12px);
}
.migrate-row {
  margin: var(--space-2) 0;
}
</style>
