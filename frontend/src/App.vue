<script setup>
/* 应用外壳：桌面端左侧栏导航 + 移动端底部 Tab Bar。
 * 原设计的横向 6 个 tab 在窄屏需要横滑且挤压品牌区，改为两套导航形态：
 *   - 桌面（>860px）：固定侧边栏，分组展示，信息层级更清晰
 *   - 移动（<=860px）：底部 Tab Bar，拇指可达，符合移动端操作习惯 */
import { computed, onMounted } from "vue";
import DashboardPanel from "./components/DashboardPanel.vue";
import ConsumptionMapPanel from "./components/ConsumptionMapPanel.vue";
import BillsPanel from "./components/BillsPanel.vue";
import AssetsPanel from "./components/AssetsPanel.vue";
import QueryPanel from "./components/QueryPanel.vue";
import CategoryPanel from "./components/CategoryPanel.vue";
import ImportPanel from "./components/ImportPanel.vue";
import SettingsPanel from "./components/SettingsPanel.vue";
import AppToast from "./components/AppToast.vue";
import AppConfirm from "./components/AppConfirm.vue";
import AppLoading from "./components/AppLoading.vue";
import AppIcon from "./components/AppIcon.vue";
import NotificationBell from "./components/NotificationBell.vue";
import ThemeToggle from "./components/ThemeToggle.vue";
import { store, refreshCategories } from "./store";

/* 导航分组：按用户的心智模型组织，而不是平铺 6 项
 * 「账本」= 日常高频的看/查，「数据」= 录入与配置，低频操作下沉 */
const NAV_GROUPS = [
  {
    label: "账本",
    items: [
      { name: "dashboard", label: "统计看板", icon: "dashboard", desc: "收支汇总、趋势与结构分析" },
      { name: "query", label: "问账", icon: "sparkles", desc: "一句话查账：口径透明、支持追问" },
      { name: "map", label: "消费地图", icon: "map", desc: "按城市查看消费分布与地域集中度" },
      { name: "bills", label: "流水管理", icon: "bills", desc: "筛选、编辑与批量处理每一笔流水" },
      { name: "assets", label: "资产管理", icon: "assets", desc: "定期记录资产与负债快照" },
    ],
  },
  {
    label: "数据",
    items: [
      { name: "import", label: "账单导入", icon: "import", desc: "上传微信、支付宝、京东、云闪付账单" },
      { name: "categories", label: "分类管理", icon: "categories", desc: "维护消费分类与自动归类口径" },
      { name: "settings", label: "设置", icon: "settings", desc: "数据库、智能分类、备份与日志" },
    ],
  },
];

const ALL_ITEMS = NAV_GROUPS.flatMap((g) => g.items);

/* 移动端底部放 6 个高频项（问账是 v0.6 主打入口，必须拇指可达）：
 * 排除低频的「分类管理」，以及偏分析性质的「消费地图」（桌面端从侧边栏进入）；
 * tab 均 flex:1 自适应宽度，6 项在窄屏仍可容纳 */
const MOBILE_EXCLUDED = ["categories", "map"];
const MOBILE_TABS = ALL_ITEMS.filter((i) => !MOBILE_EXCLUDED.includes(i.name));

const current = computed(() => ALL_ITEMS.find((i) => i.name === store.tab) || ALL_ITEMS[0]);

onMounted(() => {
  refreshCategories().catch(() => {
    /* 后端未就绪时忽略，各面板加载时会再提示 */
  });
});
</script>

<template>
  <div class="app-shell">
    <!-- 桌面端侧边导航 -->
    <aside class="sidebar">
      <div class="sidebar__brand">
        <div class="sidebar__logo">财</div>
        <div class="sidebar__title">
          <span class="sidebar__name">财务统计</span>
          <span class="sidebar__sub">fn-finstat</span>
        </div>
      </div>

      <nav class="sidebar__nav" aria-label="主导航">
        <template v-for="g in NAV_GROUPS" :key="g.label">
          <div class="nav-group__label">{{ g.label }}</div>
          <button
            v-for="item in g.items"
            :key="item.name"
            class="nav-item"
            :class="{ 'is-active': store.tab === item.name }"
            :aria-current="store.tab === item.name ? 'page' : undefined"
            :title="item.desc"
            @click="store.tab = item.name"
          >
            <AppIcon class="nav-item__icon" :name="item.icon" :size="19" />
            <span>{{ item.label }}</span>
          </button>
        </template>
      </nav>

      <div class="sidebar__footer">
        <ThemeToggle wide />
      </div>
    </aside>

    <!-- 主内容区 -->
    <div class="app-main">
      <header class="topbar">
        <div class="topbar__heading">
          <h1 class="topbar__title">{{ current.label }}</h1>
          <span class="topbar__desc">{{ current.desc }}</span>
        </div>
        <div class="topbar__actions">
          <button
            class="btn ghost icon"
            title="账单导入"
            aria-label="账单导入"
            @click="store.tab = 'import'"
          >
            <AppIcon name="import" :size="18" />
          </button>
          <NotificationBell />
          <ThemeToggle class="topbar__theme" />
        </div>
      </header>

      <main class="container">
        <DashboardPanel />
        <QueryPanel />
        <ConsumptionMapPanel />
        <BillsPanel />
        <AssetsPanel />
        <CategoryPanel />
        <ImportPanel />
        <SettingsPanel />
      </main>
    </div>

    <!-- 移动端底部 Tab Bar -->
    <nav class="mobile-tabbar" aria-label="主导航">
      <div class="mobile-tabbar__inner">
        <button
          v-for="item in MOBILE_TABS"
          :key="item.name"
          class="mobile-tab"
          :class="{ 'is-active': store.tab === item.name }"
          :aria-current="store.tab === item.name ? 'page' : undefined"
          @click="store.tab = item.name"
        >
          <AppIcon class="mobile-tab__icon" :name="item.icon" :size="21" />
          <span>{{ item.label }}</span>
        </button>
      </div>
    </nav>

    <AppToast />
    <AppConfirm />
    <AppLoading />
  </div>
</template>

<style scoped>
/* 主题入口去重：桌面端由侧边栏底部常驻（低干扰但始终可见），
   移动端侧边栏整体隐藏，改由顶栏提供，保证任何形态下都能一键切换 */
.topbar__theme {
  display: none;
}
@media (max-width: 860px) {
  .topbar__theme {
    display: inline-flex;
  }
}
</style>
