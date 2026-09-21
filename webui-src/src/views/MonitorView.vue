<template>
  <div class="monitor">
    <div class="m-toolbar">
      <n-space align="center" :size="10" wrap>
        <n-radio-group v-model:value="days" size="small" @update:value="load">
          <n-radio-button v-for="d in DAY_OPTIONS" :key="d" :value="d">
            {{ t("monitor.dayN", { n: d }) }}
          </n-radio-button>
        </n-radio-group>
        <n-checkbox v-model:checked="onlyIssues">{{ t("monitor.onlyIssues") }}</n-checkbox>
        <n-input
          v-model:value="keyword"
          size="small"
          :placeholder="t('monitor.searchPh')"
          clearable
          style="width: 180px"
        />
        <n-button size="small" quaternary :loading="loading" @click="load">
          {{ t("detect.btnRefresh") }}
        </n-button>
      </n-space>
      <n-button size="small" tertiary @click="showCaliber = !showCaliber">
        {{ t("monitor.caliberToggle") }}
      </n-button>
    </div>

    <n-alert v-if="showCaliber" type="info" :show-icon="false" class="m-caliber" closable @close="showCaliber = false">
      <div class="cal-title">{{ t("monitor.caliberTitle") }}</div>
      <ul class="cal-list">
        <li>{{ t("monitor.caliber1") }}</li>
        <li>{{ t("monitor.caliber2") }}</li>
        <li>{{ t("monitor.caliber3") }}</li>
      </ul>
    </n-alert>

    <n-alert v-if="!loading && !data?.live_available" type="warning" class="m-warn">
      {{ t("monitor.liveUnavailable") }}
    </n-alert>
    <n-alert v-else-if="data?.truncated" type="warning" class="m-warn">
      {{ t("monitor.truncated", { n: data.samples }) }}
    </n-alert>

    <n-grid :x-gap="10" :y-gap="10" cols="2 s:3 m:6" responsive="screen" class="m-summary">
      <n-gi v-for="c in summaryCards" :key="c.key">
        <n-card size="small" class="sum-card" :class="'sum-' + c.key">
          <div class="sum-num" :style="{ color: c.color }">{{ c.value }}</div>
          <div class="sum-label">{{ c.label }}</div>
        </n-card>
      </n-gi>
    </n-grid>

    <n-card v-if="alerts.length" size="small" :title="t('monitor.alertsTitle')" class="m-alerts">
      <div v-for="a in alerts" :key="a.provider_id + a.kind + a.opened_at" class="alert-row">
        <n-tag size="small" :type="a.kind === 'fail_burst' ? 'error' : 'warning'" round>
          {{ t("monitor.kind." + a.kind) }}
        </n-tag>
        <span class="alert-name">{{ a.name }}</span>
        <span class="alert-detail">{{ a.detail }}</span>
        <n-tag v-if="a.pending" size="tiny" type="info" :bordered="false">
          {{ t("monitor.pendingRetry") }}
        </n-tag>
        <span class="alert-time">{{ fmtTime(a.opened_at) }}</span>
      </div>
    </n-card>

    <n-data-table
      :columns="columns"
      :data="shown"
      :loading="loading"
      :row-key="(r: HealthItem) => r.id"
      :row-class-name="rowClass"
      size="small"
      striped
      :scroll-x="1080"
      :pagination="pagination"
    >
      <template #empty>
        <span class="m-empty">{{ emptyText }}</span>
      </template>
    </n-data-table>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NAlert, NButton, NCard, NCheckbox, NDataTable, NGi, NGrid, NInput,
  NRadioGroup, NRadioButton, NSpace, NTag,
} from "naive-ui";

import {
  apiGetHealth, fmtMs, fmtSuccess,
  type HealthItem, type HealthResponse, type HealthState,
} from "../api";

const { t } = useI18n();

const DAY_OPTIONS = [1, 3, 7, 30];
const STATE_ORDER: Record<HealthState, number> = { down: 0, degraded: 1, unknown: 2, healthy: 3 };
const STATE_COLOR: Record<HealthState, string> = {
  healthy: "#4ade80",
  degraded: "#fbbf24",
  down: "#f87171",
  unknown: "#94a3b8",
};

const data = ref<HealthResponse | null>(null);
const days = ref(7);
const loading = ref(false);
const onlyIssues = ref(false);
const keyword = ref("");
// 口径说明默认展开：这页的数字如果被当成「模型延迟」去横向比较，结论会是错的
const showCaliber = ref(true);
const pagination = { pageSize: 20, showSizePicker: true, pageSizes: [20, 50, 100] };

const items = computed<HealthItem[]>(() => data.value?.items ?? []);
const alerts = computed(() => data.value?.alerts ?? []);

const shown = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  return items.value
    .filter((it) => !onlyIssues.value || it.state === "down" || it.state === "degraded")
    .filter((it) => !kw || (it.display_model || "").toLowerCase().includes(kw))
    .slice()
    .sort((a, b) => {
      const d = STATE_ORDER[a.state] - STATE_ORDER[b.state];
      return d !== 0 ? d : (b.live?.total || 0) - (a.live?.total || 0);
    });
});

const emptyText = computed(() => {
  if (!items.value.length) return t("monitor.emptyNoModels");
  return t("monitor.emptyNoMatch");
});

const summaryCards = computed(() => {
  const c = data.value?.counts || {};
  return [
    { key: "healthy", label: t("monitor.sumHealthy"), value: c.healthy || 0, color: STATE_COLOR.healthy },
    { key: "degraded", label: t("monitor.sumDegraded"), value: c.degraded || 0, color: STATE_COLOR.degraded },
    { key: "down", label: t("monitor.sumDown"), value: c.down || 0, color: STATE_COLOR.down },
    { key: "unknown", label: t("monitor.sumUnknown"), value: c.unknown || 0, color: STATE_COLOR.unknown },
    { key: "muted", label: t("monitor.sumMuted"), value: data.value?.muted_count || 0, color: "#64748b" },
    { key: "samples", label: t("monitor.sumSamples"), value: data.value?.samples || 0, color: "#a78bfa" },
  ];
});

function fmtTime(ts: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function billingText(it: HealthItem): string {
  const b = it.billing;
  const label = t("monitor.billing." + b.type);
  if (b.type === "temp_free" && b.free_days_left !== null) {
    return b.free_days_left < 0
      ? `${label} · ${t("monitor.expired")}`
      : `${label} · ${t("monitor.daysLeft", { n: b.free_days_left })}`;
  }
  return label;
}

/** 三通道用三个小点表示，比三个开关更省地方；开关在档案页里编辑。 */
function scopeDots(it: HealthItem) {
  const s = it.scope;
  const marks: Array<[key: string, on: boolean]> = [
    ["manual", s.manual], ["scheduled", s.scheduled], ["command", s.command],
  ];
  return h("div", { class: "scope-dots" }, marks.map(([k, on]) =>
    h("span", {
      class: ["dot", on ? "dot-on" : "dot-off"],
      title: t("monitor.scope." + k) + (on ? t("monitor.scopeOn") : t("monitor.scopeOff")),
    }, k[0].toUpperCase())
  ));
}

const columns = computed(() => [
  {
    title: t("monitor.colState"), key: "state", width: 110, fixed: "left" as const,
    render: (it: HealthItem) => h("div", { class: "state-cell" }, [
      h("span", { class: "state-dot", style: { background: STATE_COLOR[it.state] } }),
      h("span", { class: "state-text" }, t("monitor.state." + it.state)),
      it.is_default ? h("span", { class: "badge-default" }, t("monitor.defaultTag")) : null,
      it.muted ? h("span", { class: "badge-muted" }, t("monitor.mutedTag")) : null,
    ]),
  },
  {
    title: t("monitor.colModel"), key: "model", minWidth: 240, ellipsis: { tooltip: true },
    render: (it: HealthItem) => h("div", { class: "model-cell" }, [
      h("div", { class: "model-name" }, it.display_model || it.model || it.id),
      it.reason ? h("div", { class: "model-reason" }, it.reason) : null,
    ]),
  },
  {
    title: t("monitor.colTtft"), key: "ttft", width: 96, align: "right" as const,
    render: (it: HealthItem) => h("span", { class: numClass(it.live.ttft_samples) }, fmtMs(it.live.avg_ttft_ms)),
  },
  {
    title: t("monitor.colRound"), key: "lat", width: 96, align: "right" as const,
    render: (it: HealthItem) => h("span", { class: numClass(it.live.latency_samples) }, fmtMs(it.live.avg_latency_ms)),
  },
  {
    title: t("monitor.colSuccess"), key: "succ", width: 92, align: "right" as const,
    render: (it: HealthItem) => h("span", { class: succClass(it) }, fmtSuccess(it.live.counted, it.live.fail_rate)),
  },
  {
    title: t("monitor.colSamples"), key: "n", width: 84, align: "right" as const,
    render: (it: HealthItem) => h("span", { class: it.live.total < 3 ? "num thin" : "num" },
      "n=" + (it.live.total || 0)),
  },
  {
    title: t("monitor.colBilling"), key: "billing", width: 150,
    render: (it: HealthItem) => h("span", { class: "billing-" + it.billing.type }, billingText(it)),
  },
  { title: t("monitor.colScope"), key: "scope", width: 96, render: scopeDots },
  {
    type: "expand" as const,
    renderExpand: (it: HealthItem) => h("div", { class: "detail" }, [
      h("div", { class: "detail-grid" }, [
        line(t("monitor.dProbe"), it.probe.checked_at
          ? `${it.probe.ok ? t("monitor.dProbeOk") : t("monitor.dProbeFail")} ${fmtMs(it.probe.latency_ms)} · ${fmtTime(it.probe.checked_at)}`
          : t("monitor.dNever")),
        line(t("monitor.dP95"), `TTFT ${fmtMs(it.live.p95_ttft_ms)} / ${t("monitor.round")} ${fmtMs(it.live.p95_latency_ms)}`),
        line(t("monitor.dBreakdown"), t("monitor.breakdown", {
          ok: it.live.ok, fail: it.live.fail, aborted: it.live.aborted,
        })),
        line(t("monitor.dTtftSamples"), t("monitor.ttftOf", {
          n: it.live.ttft_samples, total: it.live.counted,
        })),
        line(t("monitor.dTokens"), (it.live.tokens || 0).toLocaleString()),
        line(t("monitor.dRole"), t("monitor.role." + it.billing.role)),
        line(t("monitor.dChannel"), t("monitor.channel." + it.billing.channel_kind)),
        line(t("monitor.dStream"), t("monitor.stream." + it.billing.supports_streaming)),
      ]),
      it.billing.note ? h("div", { class: "detail-note" }, it.billing.note) : null,
      it.live.ttft_samples === 0 && it.live.counted > 0
        ? h("div", { class: "detail-hint" }, t("monitor.hintNoTtft"))
        : null,
    ]),
  },
]);

function line(label: string, value: string) {
  return h("div", { class: "d-item" }, [h("span", { class: "d-label" }, label), h("span", { class: "d-value" }, value)]);
}

function numClass(samples: number) {
  return samples > 0 ? "num" : "num thin";
}

function succClass(it: HealthItem) {
  if (!it.live.counted) return "num thin";
  return it.live.fail_rate >= 0.5 ? "num bad" : it.live.fail_rate > 0.1 ? "num warn" : "num good";
}

function rowClass(it: HealthItem) {
  return "row-" + it.state + (it.muted ? " row-muted" : "");
}

async function load() {
  loading.value = true;
  try {
    data.value = await apiGetHealth(days.value);
  } catch (e: any) {
    data.value = null;
    window.$message?.error?.(e?.message || String(e));
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.monitor { display: flex; flex-direction: column; gap: 12px; }
.m-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
.m-caliber .cal-title { font-weight: 600; margin-bottom: 4px; }
.cal-list { margin: 0; padding-left: 18px; line-height: 1.7; font-size: 12px; opacity: .85; }
.m-warn { font-size: 13px; }
.sum-card { text-align: center; }
.sum-num { font-size: 22px; font-weight: 700; line-height: 1.3; }
.sum-label { font-size: 12px; opacity: .65; }
.m-alerts :deep(.alert-row) { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 13px; }
.alert-name { font-weight: 600; }
.alert-detail { opacity: .75; flex: 1; }
.alert-time { font-size: 12px; opacity: .5; }
.m-empty { display: block; padding: 24px; text-align: center; opacity: .6; }

:deep(.state-cell) { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
:deep(.state-dot) { width: 8px; height: 8px; border-radius: 50%; flex: none; }
:deep(.state-text) { font-size: 13px; }
:deep(.badge-default), :deep(.badge-muted) {
  font-size: 10px; padding: 1px 5px; border-radius: 4px; line-height: 1.5;
}
:deep(.badge-default) { background: rgba(139, 92, 246, .18); color: #a78bfa; }
:deep(.badge-muted) { background: rgba(148, 163, 184, .18); color: #94a3b8; }
:deep(.model-cell) { display: flex; flex-direction: column; gap: 2px; }
:deep(.model-name) { font-size: 13px; }
:deep(.model-reason) { font-size: 11px; opacity: .6; }
:deep(.num) { font-variant-numeric: tabular-nums; font-size: 13px; }
:deep(.thin) { opacity: .35; }
:deep(.good) { color: #4ade80; }
:deep(.warn) { color: #fbbf24; }
:deep(.bad) { color: #f87171; font-weight: 600; }
:deep(.scope-dots) { display: flex; gap: 4px; }
:deep(.dot) {
  width: 18px; height: 18px; border-radius: 5px; font-size: 10px; font-weight: 700;
  display: flex; align-items: center; justify-content: center; cursor: default;
}
:deep(.dot-on) { background: rgba(74, 222, 128, .16); color: #4ade80; }
:deep(.dot-off) { background: rgba(148, 163, 184, .1); color: rgba(148, 163, 184, .45); }
:deep(.row-down) { background: rgba(248, 113, 113, .07) !important; }
:deep(.row-degraded) { background: rgba(251, 191, 36, .06) !important; }
:deep(.row-muted) { opacity: .55; }
:deep(.detail) { padding: 6px 4px 10px; display: flex; flex-direction: column; gap: 6px; }
:deep(.detail-grid) { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 4px 20px; }
:deep(.d-item) { display: flex; gap: 8px; font-size: 12px; }
:deep(.d-label) { min-width: 72px; opacity: .55; flex: none; }
:deep(.d-value) { opacity: .9; }
:deep(.detail-note) { font-size: 12px; opacity: .7; border-left: 2px solid rgba(139, 92, 246, .4); padding-left: 8px; }
:deep(.detail-hint) { font-size: 12px; color: #fbbf24; opacity: .85; }
</style>
