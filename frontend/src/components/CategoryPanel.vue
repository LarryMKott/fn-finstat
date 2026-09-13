<script setup>
/* 分类管理：预置与自定义分类的增删改。
 * 改造点：新增区并入卡片、编辑态更紧凑；说明文案区分「内置」与可编辑分类 */
import { ref } from "vue";
import { api } from "../api";
import { categories, refreshCategories, store } from "../store";
import { toast } from "../toast";
import AppIcon from "./AppIcon.vue";

const newName = ref("");
const editingId = ref(null);
const editName = ref("");

async function add() {
  const name = newName.value.trim();
  if (!name) {
    toast("请输入分类名称", true);
    return;
  }
  try {
    await api("/api/category", { method: "POST", body: JSON.stringify({ name }) });
    newName.value = "";
    toast("分类已新增");
    refreshCategories().catch(() => {});
  } catch (err) {
    toast(err.message, true);
  }
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
  try {
    const res = await api(`/api/category/${editingId.value}`, {
      method: "PUT",
      body: JSON.stringify({ name }),
    });
    editingId.value = null;
    toast(res.renamed_bills > 0 ? `已重命名，${res.renamed_bills} 条流水同步更新` : "分类已更新");
    refreshCategories().catch(() => {});
  } catch (err) {
    toast(err.message, true);
  }
}

async function remove(c) {
  try {
    /* 先取详情拿流水数，删除前给出明确告知 */
    const detail = await api(`/api/category/${c.id}`);
    const msg =
      detail.bill_count > 0
        ? `「${c.name}」下有 ${detail.bill_count} 条流水，删除后将归入「其他」，确定删除吗？`
        : `确定删除分类「${c.name}」吗？`;
    if (!confirm(msg)) return;
    const res = await api(`/api/category/${c.id}`, { method: "DELETE" });
    toast(res.moved_bills > 0 ? `已删除，${res.moved_bills} 条流水归入「其他」` : "已删除");
    refreshCategories().catch(() => {});
  } catch (err) {
    toast(err.message, true);
  }
}
</script>

<template>
  <section class="panel" :class="{ active: store.tab === 'categories' }">
    <div class="chart-box">
      <div class="section-head">
        <h3>新增分类</h3>
        <span class="section-head__hint">
          分类为全账号共享；内置关键词会自动归类，未命中的流水归入「其他」
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
        <button class="btn primary" @click="add">
          <AppIcon name="plus" :size="15" /> 新增
        </button>
      </div>
    </div>

    <div class="chart-box">
      <div class="section-head">
        <h3>全部分类</h3>
        <span class="section-head__hint">共 {{ categories.length }} 个 · 改名会同步更新已归类流水</span>
      </div>
      <ul class="category-list">
        <li v-if="!categories.length" class="empty" style="background: none">暂无分类</li>
        <li v-for="c in categories" :key="c.id">
          <template v-if="editingId === c.id">
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
              <button class="btn mini" @click="saveEdit">保存</button>
              <button class="btn mini" @click="cancelEdit">取消</button>
            </span>
          </template>
          <template v-else>
            <span class="cat-name">{{ c.name }}</span>
            <span v-if="c.name === '其他'" class="cat-default" title="自动归类的兜底分类，不可编辑">内置</span>
            <span v-else class="cat-actions">
              <button class="btn mini" @click="startEdit(c)">编辑</button>
              <button class="btn mini danger" @click="remove(c)">删除</button>
            </span>
          </template>
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.add-row {
  margin-bottom: 0;
}
.category-list {
  box-shadow: none;
  border: none;
  padding: 0;
}
</style>
