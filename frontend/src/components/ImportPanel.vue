<script setup>
/* 账单导入：NAS 目录导入 + 四平台手动上传（子组件）+ 统一的导入结果摘要。
 * NAS 浏览器与平台上传各自管理状态，仅把导入结果上抛到这里统一展示。 */
import { ref } from "vue";
import { store } from "../store";
import NasImportBox from "./import/NasImportBox.vue";
import PlatformUploadGrid from "./import/PlatformUploadGrid.vue";
import AppIcon from "./AppIcon.vue";
import { fmtMoney } from "../utils/format";

const importResult = ref(null);
const showDetails = ref(false);

function showResult(result) {
  importResult.value = result;
  showDetails.value = false;
}
</script>

<template>
  <section class="panel" :class="{ active: store.tab === 'import' }">
    <NasImportBox @result="showResult" />
    <PlatformUploadGrid @result="showResult" />

    <!-- 导入结果：结构化摘要，关键数字可快速扫读 -->
    <div class="import-result" :class="{ show: !!importResult }">
      <template v-if="importResult">
        <template v-if="importResult.ok">
          <div class="import-result__title ok">
            <AppIcon name="restore" :size="16" style="transform: rotate(180deg)" />
            导入完成<span v-if="importResult.name" class="import-result__name">
              · {{ importResult.name }}</span
            >
          </div>
          <dl class="import-stats">
            <div class="import-stat">
              <dt>解析</dt>
              <dd>{{ importResult.total }}</dd>
            </div>
            <div class="import-stat import-stat--ok">
              <dt>新增入库</dt>
              <dd>{{ importResult.inserted }}</dd>
            </div>
            <div class="import-stat">
              <dt>重复跳过</dt>
              <dd>{{ importResult.skipped_dup ?? importResult.skipped ?? 0 }}</dd>
            </div>
            <div class="import-stat" :class="{ 'import-stat--fail': importResult.failed > 0 }">
              <dt>失败</dt>
              <dd>{{ importResult.failed ?? 0 }}</dd>
            </div>
            <div v-if="importResult.ai_classified" class="import-stat">
              <dt>AI 归类</dt>
              <dd>{{ importResult.ai_classified }}</dd>
            </div>
          </dl>
          <button
            v-if="importResult.details?.length"
            class="import-details__toggle"
            :aria-expanded="showDetails"
            @click="showDetails = !showDetails"
          >
            <AppIcon
              name="chevronDown"
              :size="14"
              class="import-details__chevron"
              :class="{ open: showDetails }"
            />
            异常明细（{{ importResult.details.length }} 条）
          </button>
          <ul v-if="showDetails && importResult.details?.length" class="import-details">
            <li v-for="(d, i) in importResult.details" :key="d.tx_id || i" class="import-details__item">
              <span class="import-details__merchant">{{ d.merchant || "（未知商户）" }}</span>
              <span class="import-details__amount tabular">{{ fmtMoney(d.amount) }}</span>
              <span class="import-details__reason">{{ d.reason || "未识别" }}</span>
            </li>
          </ul>
          <p class="hint import-result__note">
            重复账单按交易单号自动去重，可放心重复上传同一份账单
          </p>
        </template>
        <div v-else class="fail">导入失败：{{ importResult.message }}</div>
      </template>
    </div>
  </section>
</template>

<style scoped>
.import-result__name {
  font-weight: 500;
  color: var(--color-text-secondary);
}
.import-stat--fail dd {
  color: var(--color-danger);
}
.import-details__toggle {
  display: flex;
  align-items: center;
  gap: var(--space-1-5);
  background: none;
  border: none;
  padding: 0;
  margin-top: var(--space-2);
  cursor: pointer;
  font-size: var(--text-xs);
  color: var(--color-primary);
}
.import-details__chevron {
  transition: transform 0.15s ease;
}
.import-details__chevron.open {
  transform: rotate(180deg);
}
.import-details {
  list-style: none;
  margin: var(--space-2) 0 0;
  padding: var(--space-2) 0 0;
  border-top: 1px dashed var(--color-border);
  display: flex;
  flex-direction: column;
  gap: var(--space-1-5);
  font-size: var(--text-xs);
}
.import-details__item {
  display: flex;
  align-items: baseline;
  gap: var(--space-2-5);
  flex-wrap: wrap;
  color: var(--color-text-secondary);
}
.import-details__merchant {
  color: var(--color-text);
  font-weight: 500;
}
.import-details__amount {
  font-variant-numeric: tabular-nums;
}
.import-details__reason {
  color: var(--color-text-tertiary);
}
</style>
