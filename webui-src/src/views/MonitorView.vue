<template>
  <div class="monitor">
    <div class="m-toolbar">
      <n-space align="center" :size="10" wrap>
        <n-radio-group v-model:value="days" size="small" @update:value="load">
          <n-radio-button v-for="d in DAY_OPTIONS" :key="d" :value="d">
            {{ t('monitor.dayN', { n: d }) }}
          </n-radio-button>
        </n-radio-group>
        <n-checkbox v-model:checked="onlyIssues">{{ t('monitor.onlyIssues') }}</n-checkbox>
        <n-input
          v-model:value="keyword"
          size="small"
          :placeholder="t('monitor.searchPh')"
          clearable
          style="width: 180px"
        />
        <n-button size="small" quaternary :loading="loading" @click="load">
          {{ t('detect.btnRefresh') }}
        </n-button>
      </n-space>
      <n-button size="small" tertiary @click="showCaliber = !showCaliber">
        {{ t('monitor.caliberToggle') }}
      </n-button>
    </div>

    <n-alert v-if="showCaliber" type="info" :show-icon="false" class="m-caliber" closable @close="showCaliber = false">
      <div class="cal-title">{{ t('monitor.caliberTitle') }}</div>
      <ul class="cal-list">
        <li>{{ t('monitor.caliber1') }}</li>
        <li>{{ t('monitor.caliber2') }}</li>
        <li>{{ t('monitor.caliber3') }}</li>
      </ul>
    </n-alert>

    <n-alert v-if="!loading && !data?.live_available" type="warning" class="m-warn">
      {{ t('monitor.liveUnavailable') }}
    </n-alert>
    <n-alert v-else-if="data?.truncated" type="warning" class="m-warn">
      {{ t('monitor.truncated', { n: data.samples }) }}
    </n-alert>

    <n-grid :x-gap="10" :y-gap="10" cols="2 s:3 m:6" responsive="screen" class="m-summary">
      <n-gi v-for="c in summaryCards" :key="c.key">
        <n-card size="small" class="sum-card">
          <div class="sum-num" :style="{ color: c.color }">{{ c.value }}</div>
          <div class="sum-label">{{ c.label }}</div>
        </n-card>
      </n-gi>
    </n-grid>

    <n-card v-if="alerts.length" size="small" :title="t('monitor.alertsTitle')" class="m-alerts">
      <div v-for="(a, i) in alerts" :key="i" class="alert-row">
        <n-tag size="small" :type="a.kind === 'fail_burst' ? 'error' : 'warning'" round>
          {{ t('monitor.kind.' + a.kind) }}
        </n-tag>
        <span class="alert-name">{{ a.name }}</span>
        <span class="alert-detail">{{ a.detail }}</span>
        <n-tag v-if="a.pending" size="tiny" type="info" :bordered="false">
          {{ t('monitor.pendingRetry') }}
        </n-tag>
        <span class="alert-time">{{ fmtTime(a.opened_at) }}</span>
      </div>
    </n-card>

    <n-empty v-if="!loading && !groups.length" :description="emptyText" class="m-empty" />

    <!-- 按供应商分组铺在一页上，不分页：换模型是「扫一眼哪家不对」的活，
         分页会把故障模型藏到第二页去 -->
    <section v-for="g in groups" :key="g.name" class="group-block">
      <header class="group-head">
        <span class="group-name">{{ g.name }}</span>
        <span class="group-tally">
          <span v-for="t in g.tally" :key="t.key" :style="{ color: t.color }">
            {{ t.n }} {{ t.label }}
          </span>
        </span>
        <span class="group-count">{{ g.items.length }} {{ t('monitor.units') }}</span>
      </header>
      <n-data-table
        :columns="columns"
        :data="g.items"
        :row-key="(r: HealthItem) => r.id"
        :row-class-name="rowClass"
        size="small"
        :pagination="false"
        :bordered="true"
        :single-line="false"
        :scroll-x="1180"
      />
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NAlert, NButton, NCard, NCheckbox, NDataTable, NEmpty, NGi, NGrid, NInput,
  NRadioGroup, NRadioButton, NSpace, NTag, NTooltip,
} from "naive-ui";
import type { DataTableColumns } from "naive-ui";

import {
  apiGetHealth, fmtMs,
  type CallSource, type HealthItem, type HealthResponse, type HealthState,
} from "../api";

const { t } = useI18n();

const DAY_OPTIONS = [1, 3, 7, 30];
const STATE_ORDER: Record<HealthState, number> = { down: 0, degraded: 1, unknown: 2, healthy: 3 };
const STATE_COLOR: Record<HealthState, string> = {
  healthy: "#4ade80", degraded: "#fbbf24", down: "#f87171", unknown: "#94a3b8",
};
const SOURCE_LABELS: Record<CallSource, string> = {
  chat: "对话", manual: "手动", command: "指令", scheduled: "定时",
};

const data = ref<HealthResponse | null>(null);
const days = ref(7);
const loading = ref(false);
const onlyIssues = ref(false);
const keyword = ref("");
// 口径说明默认展开：这页的数字若被当成「模型延迟」去横向比较，结论会是错的
const showCaliber = ref(true);

const items = computed<HealthItem[]>(() => data.value?.items ?? []);
const alerts = computed(() => data.value?.alerts ?? []);

const shown = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  return items.value
    .filter((it) => !onlyIssues.value || it.state === "down" || it.state === "degraded")
    .filter((it) => !kw
      || `${it.display_model} ${it.model} ${it.name}`.toLowerCase().includes(kw));
});

/** 按供应商分组；组内按严重度排，组间按「最差的模型」排，出问题的供应商浮上来。 */
const groups = computed(() => {
  const map = new Map<string, HealthItem[]>();
  for (const it of shown.value) {
    const key = it.name || t("monitor.ungrouped");
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(it);
  }
  const worst = (list: HealthItem[]) =>
    Math.min(...list.map((x) => STATE_ORDER[x.state] ?? 9));
  return [...map.entries()]
    .map(([name, list]) => ({
      name,
      items: [...list].sort((a, b) =>
        (STATE_ORDER[a.state] - STATE_ORDER[b.state])
        || ((b.window?.total || 0) - (a.window?.total || 0))),
      tally: (["down", "degraded", "unknown", "healthy"] as HealthState[])
        .map((k) => ({
          key: k,
          label: t("monitor.state." + k),
          color: STATE_COLOR[k],
          n: list.filter((x) => x.state === k).length,
        })).filter((x) => x.n > 0),
    }))
    .sort((a, b) => worst(a.items) - worst(b.items) || a.name.localeCompare(b.name, "zh"));
});

const emptyText = computed(() =>
  items.value.length ? t("monitor.emptyNoMatch") : t("monitor.emptyNoModels"));

const summaryCards = computed(() => {
  const c = data.value?.counts || {};
  return [
    { key: "healthy", label: t("monitor.sumHealthy"), value: c.healthy || 0, color: STATE_COLOR.healthy },
    { key: "degraded", label: t("monitor.sumDegraded"), value: c.degraded || 0, color: STATE_COLOR.degraded },
    { key: "down", label: t("monitor.sumDown"), value: c.down || 0, color: STATE_COLOR.down },
    { key: "unknown", label: t("monitor.sumUnknown"), value: c.unknown || 0, color: STATE_COLOR.unknown },
    { key: "muted", label: t("monitor.sumMuted"), value: data.value?.muted_count || 0, color: "#64748b" },
    { key: "calls", label: t("monitor.sumCalls"), value: data.value?.samples || 0, color: "#a78bfa" },
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

/** 成功率：窗口内没有任何调用时返回 null，显示 – 而不是 100%。 */
function successCell(it: HealthItem) {
  const w = it.window;
  if (!w || !w.counted) return h("span", { class: "num thin" }, "–");
  const rate = w.success_rate ?? 0;
  const cls = rate >= 0.995 ? "good" : rate >= 0.9 ? "warn" : "bad";
  return h("span", { class: `num ${cls}` }, (rate * 100).toFixed(1) + "%");
}

function lastCell(it: HealthItem) {
  const last = it.last;
  if (!last) return h("span", { class: "num thin" }, t("monitor.noCall"));
  const verdict = last.ok ? "正常" : (last.aborted ? "打断" : "失败");
  const vCls = last.ok ? "good" : (last.aborted ? "thin" : "bad");
  const lat = fmtMs(last.ttft_ms ?? last.latency_ms);
  return h("div", { class: "last-cell" }, [
    h("div", { class: "last-main" }, [
      h("span", { class: `num ${vCls}` }, verdict),
      h("span", { class: "num" }, lat),
      h("span", { class: "src-tag" }, SOURCE_LABELS[last.source] || last.source),
      !last.ok && last.error_code ? h("span", { class: "err-code" }, last.error_code) : null,
    ]),
    h("div", { class: "last-time" }, fmtTime(last.ts)),
  ]);
}

function scopeDots(it: HealthItem) {
  const s = it.scope;
  const marks: Array<[string, boolean]> = [["manual", s.manual], ["scheduled", s.scheduled], ["command", s.command]];
  return h("div", { class: "scope-dots" }, marks.map(([k, on]) =>
    h("span", {
      class: ["dot", on ? "dot-on" : "dot-off"],
      title: t("monitor.scope." + k) + (on ? t("monitor.scopeOn") : t("monitor.scopeOff")),
    }, k[0].toUpperCase())
  ));
}

function line(label: string, value: string) {
  return h("div", { class: "d-item" }, [
    h("span", { class: "d-label" }, label), h("span", { class: "d-value" }, value)]);
}

const columns = computed<DataTableColumns<HealthItem>>(() => [
  {
    title: t("monitor.colState"), key: "state", width: 104, fixed: "left",
    render: (it) => h("div", { class: "state-cell" }, [
      h("span", { class: "state-dot", style: { background: STATE_COLOR[it.state] } }),
      h("span", { class: "state-text" }, t("monitor.state." + it.state)),
      it.is_default ? h("span", { class: "badge-default" }, t("monitor.defaultTag")) : null,
      it.muted ? h("span", { class: "badge-muted" }, t("monitor.mutedTag")) : null,
    ]),
  },
  {
    title: t("monitor.colModel"), key: "model", minWidth: 200, ellipsis: { tooltip: true },
    render: (it) => h("div", { class: "model-cell" }, [
      // 组头已经写了供应商，这里再走 display_model 等于同一件事说两遍
      h("div", { class: "model-name" }, it.model || it.display_model || it.id),
      it.reason ? h("div", { class: "model-reason" }, it.reason) : null,
    ]),
  },
  { title: t("monitor.colLast"), key: "last", width: 210, render: lastCell },
  { title: t("monitor.colSuccess"), key: "succ", width: 88, align: "right", render: successCell },
  {
    title: t("monitor.colAvgTtft"), key: "ttft", width: 96, align: "right",
    render: (it) => h("span", { class: (it.window?.ttft_samples || 0) > 0 ? "num" : "num thin" },
      fmtMs(it.window?.avg_ttft_ms)),
  },
  {
    title: t("monitor.colProbeLat"), key: "probe", width: 96, align: "right",
    render: (it) => h("span", { class: (it.window?.probe_latency_samples || 0) > 0 ? "num" : "num thin" },
      fmtMs(it.window?.probe_avg_latency_ms)),
  },
  {
    title: t("monitor.colSamples"), key: "n", width: 82, align: "right",
    render: (it) => h(NTooltip, { trigger: "hover" }, {
      default: () => t("monitor.sourceSplit", {
        chat: it.window?.chat?.total ?? 0, probe: it.window?.probe?.total ?? 0,
      }),
      trigger: () => h("span", { class: (it.window?.total || 0) < 3 ? "num thin" : "num" },
        "n=" + (it.window?.total || 0)),
    }),
  },
  {
    title: t("monitor.colBilling"), key: "billing", width: 140,
    render: (it) => h("span", null, billingText(it)),
  },
  { title: t("monitor.colScope"), key: "scope", width: 92, render: scopeDots },
  {
    type: "expand",
    renderExpand: (it) => {
      const w = it.window || {};
      return h("div", { class: "detail" }, [
        h("div", { class: "detail-grid" }, [
          line(t("monitor.dSplit"), t("monitor.splitDetail", {
            chat: w.chat?.total ?? 0, chatFail: w.chat?.fail ?? 0,
            probe: w.probe?.total ?? 0, probeFail: w.probe?.fail ?? 0,
            aborted: w.aborted ?? 0,
          })),
          line(t("monitor.dP95"), `${t("monitor.ttft")} ${fmtMs(w.p95_ttft_ms)} / ${t("monitor.round")} ${fmtMs(w.p95_latency_ms)} / ${t("monitor.probe")} ${fmtMs(w.probe_p95_latency_ms)}`),
          line(t("monitor.dTtftSamples"), t("monitor.ttftOf", { n: w.ttft_samples ?? 0, total: w.counted ?? 0 })),
          line(t("monitor.dProbeSamples"), String(w.probe_latency_samples ?? 0)),
          line(t("monitor.dTokens"), (w.tokens || 0).toLocaleString()),
          line(t("monitor.dRole"), t("monitor.role." + it.billing.role)),
          line(t("monitor.dChannel"), t("monitor.channel." + it.billing.channel_kind)),
          line(t("monitor.dStream"), t("monitor.stream." + it.billing.supports_streaming)),
          line(t("monitor.dConsec"), String(it.consecutive_fail || 0)),
        ]),
        it.billing.note ? h("div", { class: "detail-note" }, it.billing.note) : null,
        (w.counted ?? 0) > 0 && (w.ttft_samples ?? 0) === 0
          ? h("div", { class: "detail-hint" }, t("monitor.hintNoTtft")) : null,
        it.muted ? h("div", { class: "detail-hint" }, t("monitor.mutedUntil", { t: fmtTime(it.muted_until) })) : null,
      ]);
    },
  },
]);

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
.m-empty { padding: 30px 0; }

.group-block { margin-bottom: 4px; }
.group-head {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 6px 10px; border-radius: 8px 8px 0 0;
  background: rgba(139, 92, 246, .10); border-left: 3px solid #8b5cf6;
}
.group-name { font-size: 14px; font-weight: 600; }
.group-tally { display: flex; gap: 10px; font-size: 12px; }
.group-count { margin-left: auto; font-size: 12px; opacity: .55; }

:deep(.state-cell) { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
:deep(.state-dot) { width: 8px; height: 8px; border-radius: 50%; flex: none; }
:deep(.state-text) { font-size: 13px; }
:deep(.badge-default), :deep(.badge-muted) { font-size: 10px; padding: 1px 5px; border-radius: 4px; line-height: 1.5; }
:deep(.badge-default) { background: rgba(139, 92, 246, .18); color: #a78bfa; }
:deep(.badge-muted) { background: rgba(148, 163, 184, .18); color: #94a3b8; }
:deep(.model-cell) { display: flex; flex-direction: column; gap: 2px; }
:deep(.model-name) { font-size: 14px; font-weight: 700; line-height: 1.4; }
:deep(.model-reason) { font-size: 11px; opacity: .6; }
:deep(.last-cell) { display: flex; flex-direction: column; gap: 2px; }
:deep(.last-main) { display: flex; align-items: center; gap: 6px; }
:deep(.last-time) { font-size: 11px; opacity: .5; }
:deep(.src-tag) {
  font-size: 10px; padding: 1px 5px; border-radius: 4px;
  background: rgba(148, 163, 184, .14); color: #9aa3c0;
}
:deep(.err-code) { font-size: 11px; color: #f87171; }
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
:deep(.detail-grid) { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 4px 20px; }
:deep(.d-item) { display: flex; gap: 8px; font-size: 12px; }
:deep(.d-label) { min-width: 76px; opacity: .55; flex: none; }
:deep(.d-value) { opacity: .9; }
:deep(.detail-note) { font-size: 12px; opacity: .7; border-left: 2px solid rgba(139, 92, 246, .4); padding-left: 8px; }
:deep(.detail-hint) { font-size: 12px; color: #fbbf24; opacity: .85; }
</style>
