<script setup>
import { ref } from "vue";
import { api } from "../api";
import { categories, store } from "../store";
import { toast } from "../toast";

const newName = ref("");

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
    refreshCategories();
  } catch (err) {
    toast(err.message, true);
  }
}
</script>

<template>
  <section class="panel" :class="{ active: store.tab === 'categories' }">
    <div class="add-row">
      <input
        v-model="newName"
        type="text"
        placeholder="输入新分类名称，如：数码"
        maxlength="20"
        @keydown.enter="add"
      />
      <button class="btn primary" @click="add">新增分类</button>
    </div>
    <ul class="category-list">
      <li v-if="!categories.length" class="empty" style="background: none; color: #94a3b8">暂无分类</li>
      <li v-for="c in categories" :key="c.id">{{ c.name }}</li>
    </ul>
  </section>
</template>
