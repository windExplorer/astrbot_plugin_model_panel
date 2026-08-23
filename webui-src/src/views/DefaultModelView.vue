<template>
  <div class="default-view">
    <n-card :title="t('defaultModel.title')" size="small">
      <div v-if="defaultId" class="value">{{ defaultId }}</div>
      <n-text v-else depth="3">{{ t("defaultModel.notSet") }}</n-text>
      <p class="note">
        <n-text depth="3">{{ t("defaultModel.note") }}</n-text>
      </p>
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { NCard, NText } from "naive-ui";

import { apiGet } from "../api";

const { t } = useI18n();
const defaultId = ref("");

onMounted(async () => {
  try {
    const data = await apiGet<{ default_provider_id: string }>("/panel/default_model");
    defaultId.value = data.default_provider_id || "";
  } catch (e) {
    console.error("加载默认模型失败", e);
  }
});
</script>

<style scoped>
.value {
  font-size: 20px;
  font-weight: 700;
  color: var(--accent-2);
}
.note {
  margin-top: 12px;
  font-size: 13px;
}
</style>
