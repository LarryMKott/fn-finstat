<script setup>
/* 设置页外壳：按业务域拆分的子卡片组合（数据库/智能分类/备份/日志/外观与关于）。
 * 原实现把全部配置块与状态机塞在一个 724 行的组件里，现拆分为
 * components/settings/ 下的独立子组件，各自管理自身状态与加载时机。 */
import { ref } from "vue";
import { store } from "../store";
import DatabaseCard from "./settings/DatabaseCard.vue";
import AICard from "./settings/AICard.vue";
import BackupCard from "./settings/BackupCard.vue";
import LogsCard from "./settings/LogsCard.vue";
import AuditCard from "./settings/AuditCard.vue";
import TokenCard from "./settings/TokenCard.vue";
import AppearanceAboutCard from "./settings/AppearanceAboutCard.vue";
import AutomationCard from "./settings/AutomationCard.vue";
import NotificationCard from "./settings/NotificationCard.vue";
import LedgerCard from "./settings/LedgerCard.vue";

const dbCard = ref(null);
</script>

<template>
  <section class="panel" :class="{ active: store.tab === 'settings' }">
    <DatabaseCard ref="dbCard" />
    <AICard />
    <AutomationCard />
    <NotificationCard />
    <LedgerCard />
    <BackupCard @restored="dbCard?.loadInfo()" />
    <LogsCard />
    <AuditCard />
    <TokenCard />
    <AppearanceAboutCard />
  </section>
</template>
