<template>
  <div class="default-view">
    <n-card :title="t('defaultModel.title')" size="small">
      <template #header-extra>
        <n-button quaternary size="small" :loading="refreshing" @click="manualRefresh">
          {{ t("detect.btnRefresh") }}
        </n-button>
      </template>
      <div v-if="defaultId" class="value">{{ defaultId }}</div>
      <n-text v-else depth="3">{{ t("defaultModel.notSet") }}</n-text>
      <div class="note">
        <n-text depth="3">{{ t("defaultModel.note") }}</n-text>
      </div>
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { NButton, NCard, NText } from "naive-ui";

import { apiGet } from "../api";

const { t } = useI18n();
const defaultId = ref("");
const refreshing = ref(false);

async function loadDefault() {
  try {
    const data = await apiGet<{ default_provider_id: string }>("/panel/default_model");
    defaultId.value = data.default_provider_id || "";
  } catch (e) {
    console.error("加载默认模型失败", e);
  }
}

async function manualRefresh() {
  if (refreshing.value) return;
  refreshing.value = true;
  try {
    await loadDefault();
  } finally {
    refreshing.value = false;
  }
}

onMounted(() => {
  loadDefault();
  // 切回本页 / 从其它标签页切回时自动刷新，保证展示最新的默认模型
  document.addEventListener("visibilitychange", onVisibility);
});

function onVisibility() {
  if (document.visibilityState === "visible") {
    loadDefault();
  }
}
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
