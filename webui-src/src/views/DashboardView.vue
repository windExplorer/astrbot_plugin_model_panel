<template>
  <div class="dashboard">
    <n-grid :cols="4" :x-gap="14" :y-gap="14" responsive="screen" :item-responsive="true">
      <n-gi span="0:4 m:1">
        <n-card size="small">
          <div class="stat-num">{{ overview?.total ?? "–" }}</div>
          <div class="stat-label">{{ t("dashboard.statTotal") }}</div>
        </n-card>
      </n-gi>
      <n-gi span="0:4 m:1">
        <n-card size="small">
          <div class="stat-num" :class="overview?.default_set ? 'tag-ok' : 'tag-warn'">
            {{ overview?.default_set ? t("dashboard.defaultSet") : t("dashboard.defaultMissed") }}
          </div>
          <div class="stat-label">{{ t("dashboard.statDefault") }}</div>
        </n-card>
      </n-gi>
      <n-gi span="0:4 m:1">
        <n-card size="small">
          <div class="stat-num" :class="overview?.companion_loaded ? 'tag-ok' : 'tag-err'">
            {{ overview?.companion_loaded ? t("dashboard.companionLoaded") : t("dashboard.companionUnloaded") }}
          </div>
          <div class="stat-label">{{ t("dashboard.statCompanion") }}</div>
        </n-card>
      </n-gi>
      <n-gi span="0:4 m:1">
        <n-card size="small">
          <div class="stat-num">{{ overview?.companion_provider_count ?? "–" }}</div>
          <div class="stat-label">{{ t("dashboard.statCompanionProviders") }}</div>
        </n-card>
      </n-gi>
    </n-grid>

    <n-card :title="t('dashboard.currentDefault')" size="small" class="mt-3">
      <template v-if="overview && overview.default_provider_id">
        <n-text>{{ overview.default_provider_id }}</n-text>
        <n-tag v-if="!overview.default_set" type="warning" size="small" style="margin-left: 8px">
          {{ t("dashboard.currentDefaultNotInList") }}
        </n-tag>
      </template>
      <n-text v-else depth="3">{{ t("dashboard.notSet") }}</n-text>
    </n-card>

    <n-card :title="t('dashboard.historyTitle')" size="small" class="mt-3">
      <template v-if="overview?.history && (overview.history.sessions_total || 0) > 0">
        <n-space vertical>
          <n-space align="center" wrap>
            <n-tag :type="overview.history.latest_session && overview.history.latest_session.alive_rate >= 80 ? 'success' : 'warning'" round>
              {{ t("dashboard.aliveRate") }} {{ overview.history.latest_session?.alive_rate ?? "–" }}%
            </n-tag>
            <n-text depth="3">
              {{ t("dashboard.latestSession") }} ·
              {{ t("dashboard.okCount") }} {{ overview.history.latest_session?.ok_count ?? 0 }} /
              {{ t("dashboard.failCount") }} {{ overview.history.latest_session?.fail_count ?? 0 }} /
              {{ t("dashboard.skipCount") }} {{ overview.history.latest_session?.skip_count ?? 0 }}
            </n-text>
          </n-space>
          <n-text depth="3">
            {{ t("dashboard.totalSessions") }} {{ overview.history.sessions_total }} ·
            {{ t("dashboard.totalChecks") }} {{ overview.history.results_total }}
            （{{ t("dashboard.okCount") }} {{ overview.history.results_ok }} /
            {{ t("dashboard.failCount") }} {{ overview.history.results_fail }}）
          </n-text>
          <n-button size="small" tag="a" :href="'#/detect'" type="primary" ghost>
            {{ t("dashboard.goDetect") }} →
          </n-button>
        </n-space>
      </template>
      <n-text v-else depth="3">{{ t("dashboard.noSession") }}</n-text>
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { NButton, NCard, NGrid, NGi, NSpace, NTag, NText } from "naive-ui";

import { apiGet, type Overview } from "../api";

const { t } = useI18n();
const overview = ref<Overview | null>(null);

onMounted(async () => {
  try {
    overview.value = await apiGet<Overview>("/panel/overview");
  } catch (e) {
    console.error("加载总览失败", e);
  }
});
</script>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
}
.mt-3 {
  margin-top: 12px;
}
.stat-num {
  font-size: 26px;
  font-weight: 700;
}
.stat-label {
  color: var(--muted);
  font-size: 13px;
  margin-top: 4px;
}
</style>
