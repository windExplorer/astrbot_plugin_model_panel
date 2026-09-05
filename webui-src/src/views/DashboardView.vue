<template>
  <div class="dashboard">
    <div class="dash-toolbar">
      <n-button quaternary :loading="refreshing" @click="manualRefresh">
        {{ t("detect.btnRefresh") }}
      </n-button>
    </div>

    <n-grid :x-gap="14" :y-gap="14" cols="1 s:2 m:4" responsive="screen">
      <n-gi>
        <n-card size="small" class="stat-card">
          <div class="stat-num">{{ overview?.total ?? "–" }}</div>
          <div class="stat-label">{{ t("dashboard.statTotal") }}</div>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small" class="stat-card">
          <n-tag
            :type="overview?.default_set ? 'success' : 'warning'"
            size="medium"
            round
          >
            {{ overview?.default_set ? t("dashboard.defaultSet") : t("dashboard.defaultMissed") }}
          </n-tag>
          <div class="stat-label">{{ t("dashboard.statDefault") }}</div>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small" class="stat-card">
          <n-tag
            :type="overview?.companion_loaded ? 'success' : 'error'"
            size="medium"
            round
          >
            {{ overview?.companion_loaded ? t("dashboard.companionLoaded") : t("dashboard.companionUnloaded") }}
          </n-tag>
          <div class="stat-label">{{ t("dashboard.statCompanion") }}</div>
        </n-card>
      </n-gi>
      <n-gi>
        <n-card size="small" class="stat-card">
          <div class="stat-num accent-num">{{ fmt(usage?.today.total ?? 0) }}</div>
          <div class="stat-label">{{ t("dashboard.statTokens") }}</div>
        </n-card>
      </n-gi>
    </n-grid>

    <n-card :title="t('dashboard.usageTitle')" size="small" class="mt-3">
      <template v-if="usage">
        <n-grid :x-gap="14" :y-gap="14" cols="1 s:3" responsive="screen">
          <n-gi>
            <div class="usage-big accent-num">{{ fmt(usage.today.total) }}</div>
            <div class="usage-sub">{{ t("dashboard.usageToday") }}</div>
          </n-gi>
          <n-gi>
            <div class="usage-big muted">{{ fmt(usage.total.total) }}</div>
            <div class="usage-sub">{{ t("dashboard.usageTotal") }}</div>
          </n-gi>
          <n-gi>
            <div class="usage-big muted">{{ usage.today.requests }}</div>
            <div class="usage-sub">{{ t("dashboard.usageRequests") }}</div>
          </n-gi>
        </n-grid>

        <div class="usage-split mt-2">
          <n-tag size="small" round>
            {{ t("dashboard.usageInput") }} {{ fmt(usage.today.input) }}
          </n-tag>
          <n-tag size="small" round>
            {{ t("dashboard.usageOutput") }} {{ fmt(usage.today.output) }}
          </n-tag>
          <n-tag v-if="usage.today.cached" size="small" round>
            {{ t("dashboard.usageCached") }} {{ fmt(usage.today.cached) }}
          </n-tag>
        </div>

        <div class="mt-3">
          <div class="section-title">{{ t("dashboard.usageTrend") }}</div>
          <div class="bars">
            <div
              v-for="d in usage.by_day"
              :key="d.day"
              class="bar-item"
              :title="`${d.day} · ${d.total}`"
            >
              <div class="bar-wrap">
                <div class="bar" :style="{ height: barHeight(d.total) }"></div>
              </div>
              <div class="bar-label">{{ d.day.slice(5) }}</div>
            </div>
          </div>
        </div>

        <div v-if="usage.by_model.length" class="mt-3">
          <div class="section-title">{{ t("dashboard.usageTopModels") }}</div>
          <div class="model-rows">
            <div
              v-for="m in usage.by_model"
              :key="m.model || m.provider_id"
              class="model-row"
            >
              <div class="model-name">
                <span class="ellipsis">{{ m.model || m.provider_id }}</span>
                <span class="model-req">× {{ m.requests }}</span>
              </div>
              <div class="model-bar">
                <div
                  class="model-bar-inner"
                  :style="{ width: modelWidth(m.total) }"
                ></div>
              </div>
              <div class="model-total">{{ fmt(m.total) }}</div>
            </div>
          </div>
        </div>

        <div class="mt-2">
          <n-text depth="3" class="hint">{{ t("dashboard.usageHint") }}</n-text>
        </div>
      </template>
      <n-text v-else depth="3">{{ t("dashboard.usageNoData") }}</n-text>
    </n-card>

    <n-card :title="t('dashboard.currentDefault')" size="small" class="mt-3">
      <template v-if="overview && overview.default_provider_id">
        <div class="default-row">
          <n-text strong>
            {{ overview.default_label || overview.default_provider_id }}
          </n-text>
          <n-text depth="3" class="mono">{{ overview.default_provider_id }}</n-text>
          <n-tag v-if="!overview.default_set" type="warning" size="small">
            {{ t("dashboard.currentDefaultNotInList") }}
          </n-tag>
        </div>
      </template>
      <n-text v-else depth="3">{{ t("dashboard.notSet") }}</n-text>
    </n-card>

    <n-card :title="t('dashboard.historyTitle')" size="small" class="mt-3">
      <template v-if="overview?.history && (overview.history.sessions_total || 0) > 0">
        <n-space vertical>
          <n-space align="center" wrap>
            <n-tag
              :type="overview.history.latest_session && overview.history.latest_session.alive_rate >= 80 ? 'success' : 'warning'"
              round
              size="medium"
            >
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
          <div>
            <n-button text type="primary" tag="a" href="#/detect">
              {{ t("dashboard.goDetect") }} →
            </n-button>
          </div>
        </n-space>
      </template>
      <n-text v-else depth="3">{{ t("dashboard.noSession") }}</n-text>
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { NButton, NCard, NGrid, NGi, NSpace, NTag, NText } from "naive-ui";

import { apiGet, type Overview, type UsageStats } from "../api";

const { t } = useI18n();
const overview = ref<Overview | null>(null);
const refreshing = ref(false);

const usage = computed<UsageStats | null>(() => overview.value?.usage ?? null);

/** 大数字缩写：12345 -> 12.3k，1234567 -> 1.2M */
function fmt(n: number): string {
  const v = Number(n || 0);
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(v >= 10_000_000 ? 0 : 1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(v >= 10_000 ? 0 : 1)}k`;
  return String(v);
}

const maxDay = computed(() =>
  Math.max(1, ...(usage.value?.by_day || []).map((d) => d.total || 0)),
);
function barHeight(v: number): string {
  const val = Number(v || 0);
  if (val <= 0) return "0";
  // 有量但很小时也保留一点可见高度
  return `${Math.max((val / maxDay.value) * 100, 3)}%`;
}

const maxModel = computed(() =>
  Math.max(1, ...(usage.value?.by_model || []).map((m) => m.total || 0)),
);
function modelWidth(v: number): string {
  return `${(Number(v || 0) / maxModel.value) * 100}%`;
}

async function loadOverview() {
  try {
    overview.value = await apiGet<Overview>("/panel/overview");
  } catch (e) {
    console.error("加载总览失败", e);
  }
}

async function manualRefresh() {
  if (refreshing.value) return;
  refreshing.value = true;
  try {
    await loadOverview();
  } finally {
    refreshing.value = false;
  }
}

function onVisibility() {
  if (document.visibilityState === "visible") {
    loadOverview();
  }
}

onMounted(() => {
  loadOverview();
  // 切回本页 / 从其它标签页切回时自动刷新，保证展示最新的默认模型与统计
  document.addEventListener("visibilitychange", onVisibility);
});

onUnmounted(() => {
  document.removeEventListener("visibilitychange", onVisibility);
});
</script>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
}
.dash-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 8px;
}
.mt-2 {
  margin-top: 8px;
}
.mt-3 {
  margin-top: 14px;
}
.stat-card {
  border-radius: 12px;
  transition: box-shadow 0.2s ease, transform 0.2s ease;
}
.stat-card:hover {
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.08);
  transform: translateY(-2px);
}
.stat-num {
  font-size: 28px;
  font-weight: 800;
  line-height: 1.15;
  font-variant-numeric: tabular-nums;
}
.accent-num {
  color: var(--accent-2);
}
.muted {
  color: var(--muted);
}
.stat-label {
  color: var(--muted);
  font-size: 13px;
  margin-top: 8px;
}
.usage-big {
  font-size: 24px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.usage-sub {
  color: var(--muted);
  font-size: 12px;
  margin-top: 4px;
}
.usage-split {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.section-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}
.bars {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  height: 112px;
}
.bar-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  height: 100%;
}
.bar-wrap {
  flex: 1;
  width: 100%;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}
.bar {
  width: 60%;
  max-width: 30px;
  background: var(--accent-2);
  opacity: 0.85;
  border-radius: 6px 6px 0 0;
  transition: height 0.3s ease;
}
.bar-label {
  font-size: 11px;
  color: var(--muted);
  margin-top: 6px;
}
.model-rows {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.model-row {
  display: grid;
  grid-template-columns: minmax(0, 2fr) 3fr auto;
  align-items: center;
  gap: 10px;
}
.model-name {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}
.ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
.model-req {
  color: var(--muted);
  font-size: 12px;
  flex: none;
}
.model-bar {
  height: 8px;
  background: rgba(128, 128, 128, 0.16);
  border-radius: 999px;
  overflow: hidden;
}
.model-bar-inner {
  height: 100%;
  background: var(--accent-2);
  border-radius: 999px;
  transition: width 0.3s ease;
}
.model-total {
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.default-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.mono {
  font-size: 12px;
  opacity: 0.75;
}
.hint {
  font-size: 12px;
}
</style>
