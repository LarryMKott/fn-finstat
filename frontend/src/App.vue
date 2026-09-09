<script setup>
import { onMounted } from "vue";
import DashboardPanel from "./components/DashboardPanel.vue";
import BillsPanel from "./components/BillsPanel.vue";
import CategoryPanel from "./components/CategoryPanel.vue";
import ImportPanel from "./components/ImportPanel.vue";
import AppToast from "./components/AppToast.vue";
import { store, refreshCategories } from "./store";

const tabs = [
  { name: "dashboard", label: "统计看板" },
  { name: "bills", label: "流水管理" },
  { name: "categories", label: "分类管理" },
  { name: "import", label: "账单导入" },
];

onMounted(() => {
  refreshCategories().catch(() => {
    /* 后端未就绪时忽略，各面板加载时会再提示 */
  });
});
</script>

<template>
  <header class="topbar">
    <div class="brand">财务统计<span class="sub">fn-finstat</span></div>
    <nav class="tabs">
      <button
        v-for="t in tabs"
        :key="t.name"
        class="tab"
        :class="{ active: store.tab === t.name }"
        @click="store.tab = t.name"
      >
        {{ t.label }}
      </button>
    </nav>
  </header>

  <main class="container">
    <DashboardPanel />
    <BillsPanel />
    <CategoryPanel />
    <ImportPanel />
  </main>

  <AppToast />
</template>
