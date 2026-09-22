<template>
  <div class="models">
    <n-alert type="info" :show-icon="false" class="m-note">
      {{ t("models.pageNote") }}
    </n-alert>

    <div class="m-toolbar">
      <n-space align="center" :size="10" wrap>
        <n-button type="primary" size="small" :loading="testing" :disabled="testing" @click="testAll">
          {{ testing ? t("detect.btnAllLoading") : t("detect.btnAll") }}
        </n-button>
        <n-button size="small" quaternary :loading="refreshing" :disabled="testing" @click="load">
          {{ t("detect.btnRefresh") }}
        </n-button>
        <n-divider vertical />
        <n-input
          v-model:value="keyword"
          size="small"
          :placeholder="t('models.searchPh')"
          clearable
          style="width: 170px"
        />
        <n-text depth="3">{{ t("detect.sortBy") }}：</n-text>
        <n-select v-model:value="sortKey" :options="sortOptions" size="small" style="width: 120px" />
        <n-divider vertical />
        <n-button size="small" tertiary @click="setAllChannel('manual', true)">
          {{ t("models.allOn") }}
        </n-button>
        <n-button size="small" tertiary @click="setAllChannel('manual', false)">
          {{ t("models.allOff") }}
        </n-button>
        <n-text depth="3" class="m-hint">{{ t("models.manualHint") }}</n-text>
      </n-space>
    </div>

    <n-alert v-if="testing" type="info" :show-icon="false" class="m-banner">
      <n-space align="center" justify="space-between" wrap>
        <n-text strong>
          {{ t("detect.testingProgress", { current: progress.current, total: progress.total }) }}
        </n-text>
        <n-text depth="3">
          {{ t("detect.testingRunning", { ok: stats?.ok_count ?? 0, fail: stats?.fail_count ?? 0 }) }}
        </n-text>
      </n-space>
      <n-progress
        type="line"
        :percentage="progress.percent"
        :show-indicator="false"
        :height="4"
        :border-radius="2"
        status="info"
      />
    </n-alert>

    <n-empty v-if="!loading && !groups.length" :description="t('models.empty')" class="m-empty" />

    <section v-for="g in groups" :key="g.name" class="group-block">
      <header class="group-head">
        <span class="group-name">{{ g.name }}</span>
        <span class="group-count">{{ g.items.length }} {{ t("monitor.units") }}</span>

        <!-- 组级一键设置：计费一个下拉、通道三个「点一下整组翻转」的芯片。
             放在组头而不是表头，是因为表头是所有组共用的列定义，
             挂到组头上才谈得上「这一组」。计费下拉刻意常驻 null：
             它是「选完即执行」的动作菜单，不是需要回显的字段。 -->
        <n-select
          :value="null"
          size="tiny"
          :options="billingOptions"
          :placeholder="t('models.setGroupBilling')"
          style="width: 132px"
          @update:value="(v: string) => applyGroupBilling(g, v)"
        />
        <n-button
          v-for="c in CHANNELS"
          :key="c"
          size="tiny"
          :type="channelTally(g, c).all ? 'primary' : 'default'"
          :ghost="channelTally(g, c).all"
          :class="{ 'chip-mixed': channelTally(g, c).mixed }"
          @click="flipGroupChannel(g, c)"
        >
          {{ t("monitor.scope." + c) }} {{ channelTally(g, c).on }}/{{ g.items.length }}
        </n-button>

        <!-- 分组级计费设置：倍率与每日次数限制天然是一家一个值，
             逐个模型填十遍既累又容易填得不一致。留空即「未设置」。 -->
        <n-tooltip :delay="400">
          <template #trigger>
            <n-input-number
              :value="vendorVal(g.name, 'rate_multiplier')"
              size="tiny"
              :show-button="false"
              :min="0"
              :step="0.1"
              :placeholder="t('models.groupRate')"
              style="width: 96px"
              @update:value="(v: number | null) => setVendorVal(g.name, 'rate_multiplier', v)"
            />
          </template>
          {{ t("models.groupVendorHint") }}
        </n-tooltip>
        <n-input-number
          :value="vendorVal(g.name, 'daily_call_limit')"
          size="tiny"
          :show-button="false"
          :min="1"
          :step="100"
          :placeholder="t('models.groupLimit')"
          style="width: 104px"
          @update:value="(v: number | null) => setVendorVal(g.name, 'daily_call_limit', v)"
        />
        <span class="limit-chip" :class="limitClass(g.name)">{{ limitText(g.name) }}</span>
        <n-tooltip :delay="400">
          <template #trigger>
            <span class="limit-chip calls-split">{{ callsSplit(g.name) }}</span>
          </template>
          {{ t("models.callsSplitHint") }}
        </n-tooltip>
        <span class="group-spacer" />
        <n-button
          size="tiny"
          type="primary"
          ghost
          :disabled="testing || refreshing"
          @click="testGroup(g)"
        >
          {{ t("detect.btnGroupTest") }}
        </n-button>
      </header>
      <n-data-table
        :columns="columns"
        :data="g.items"
        :row-key="(r: HealthItem) => r.id"
        size="small"
        :pagination="false"
        :bordered="true"
        :single-line="false"
        :scroll-x="1258"
      />
    </section>

    <n-card v-if="stats" size="small" class="m-stats">
      <n-space align="center" wrap>
        <n-tag :type="stats.ok_count ? 'success' : 'default'" round>✓ {{ stats.ok_count }}</n-tag>
        <n-tag :type="stats.fail_count ? 'error' : 'default'" round>✗ {{ stats.fail_count }}</n-tag>
        <n-tag v-if="stats.skip_count" type="warning" round>⤴ {{ stats.skip_count }}</n-tag>
        <n-text depth="3">
          {{ t("detect.streamDoneHint", {
            ok: stats.ok_count, fail: stats.fail_count, skip: stats.skip_count,
          }) }}
        </n-text>
      </n-space>
    </n-card>

    <div v-if="dirtyCount" class="m-savebar">
      <span class="save-hint">{{ t("profile.dirtyCount", { n: dirtyCount }) }}</span>
      <n-space :size="8">
        <n-button size="small" tertiary @click="discard">{{ t("profile.discard") }}</n-button>
        <n-button size="small" type="primary" :loading="saving" @click="saveAll">
          {{ t("profile.saveAll") }}
        </n-button>
      </n-space>
    </div>

    <n-modal
      v-model:show="detailVisible"
      preset="card"
      :title="t('detect.detailTitle')"
      style="max-width: 720px; width: calc(100vw - 48px)"
    >
      <n-descriptions v-if="detailRow" :column="1" size="small" bordered label-placement="left">
        <n-descriptions-item :label="t('detect.colModel')">
          {{ detailName }} · {{ detailRow.model || "—" }}
        </n-descriptions-item>
        <n-descriptions-item :label="t('detect.detail.id')">{{ detailRow.id }}</n-descriptions-item>
        <n-descriptions-item :label="t('detect.detail.checked_at')">
          {{ formatTime(detailRow.checked_at) }}
        </n-descriptions-item>
        <n-descriptions-item :label="t('detect.detail.ok')">
          <n-tag :type="detailRow.ok ? 'success' : 'error'" size="small">
            {{ detailRow.ok ? t("detect.result.ok") : t("detect.result.fail") }}
          </n-tag>
        </n-descriptions-item>
        <n-descriptions-item :label="t('detect.detail.latency_ms')">
          {{ detailRow.latency_ms ?? "—" }}
        </n-descriptions-item>
        <n-descriptions-item :label="t('detect.detail.error_code')">
          {{ detailRow.error_code || "—" }}
        </n-descriptions-item>
        <n-descriptions-item :label="t('detect.detail.error')">
          {{ detailRow.error || "—" }}
        </n-descriptions-item>
        <n-descriptions-item :label="t('detect.detail.retry_count')">
          {{ detailRow.retry_count ?? 0 }}
        </n-descriptions-item>
        <n-descriptions-item :label="t('detect.detail.skipped')">
          {{ detailRow.skipped ? t("common.yes") : t("common.no") }}
        </n-descriptions-item>
      </n-descriptions>
      <n-divider />
      <div class="m-rawlabel">{{ t("detect.detail.rawJson") }}</div>
      <n-code :code="jsonText(detailRow)" language="json" :show-line-numbers="false" />
    </n-modal>

    <n-drawer v-model:show="drawerOpen" :width="420" placement="right">
      <n-drawer-content :title="drawerTitle" closable>
        <n-form v-if="editing" label-placement="top" size="small">
          <div class="drawer-sub">{{ editing.display_model }} · {{ editing.id }}</div>

          <n-form-item :label="t('profile.fChannel')">
            <n-select :value="val(editing, 'channel_kind')" :options="channelOptions"
              @update:value="(v: string) => setVal(editing!, 'channel_kind', v)" />
          </n-form-item>
          <n-form-item :label="t('monitor.dRole')">
            <n-select :value="val(editing, 'role')" :options="roleOptions"
              @update:value="(v: string) => setVal(editing!, 'role', v)" />
          </n-form-item>
          <n-form-item :label="t('profile.fStream')">
            <n-select :value="val(editing, 'supports_streaming')" :options="streamOptions"
              @update:value="(v: string) => setVal(editing!, 'supports_streaming', v)" />
          </n-form-item>
          <n-form-item :label="t('profile.fProbeMode')">
            <n-select :value="val(editing, 'probe_mode')" :options="probeModeOptions"
              @update:value="(v: string) => setVal(editing!, 'probe_mode', v)" />
            <div class="drawer-hint">{{ t("profile.probeModeHint") }}</div>
          </n-form-item>

          <n-divider>{{ t("profile.priceGroup") }}</n-divider>
          <n-form-item :label="t('profile.fCurrency')">
            <n-select :value="val(editing, 'currency')" :options="currencyOptions" clearable
              @update:value="(v: string) => setVal(editing!, 'currency', v)" />
          </n-form-item>
          <n-form-item v-for="p in PRICE_FIELDS" :key="p.key" :label="p.label(t)">
            <n-input-number
              :value="val(editing, p.key)"
              @update:value="(v: number | null) => setVal(editing!, p.key, v)"
              size="small"
              :min="0"
              :step="p.step"
              :precision="p.precision"
              clearable
              style="width: 100%"
            >
              <template #suffix>{{ t(p.unitKey) }}</template>
            </n-input-number>
          </n-form-item>
          <div class="drawer-hint">{{ t("profile.priceHint") }}</div>
          <div class="drawer-hint">{{ t("profile.perCallHint") }}</div>

          <n-form-item :label="t('profile.fRateMultiplier')">
            <n-input-number
              :value="val(editing, 'rate_multiplier')"
              @update:value="(v: number | null) => setVal(editing!, 'rate_multiplier', v)"
              size="small"
              :min="0"
              :step="0.01"
              :precision="4"
              clearable
              style="width: 100%"
            >
              <template #suffix>× {{ effectiveMultiplier(editing) }}</template>
            </n-input-number>
            <div class="drawer-hint">{{ t("profile.rateHint") }}</div>
          </n-form-item>

          <n-divider>{{ t("profile.scopeGroup") }}</n-divider>
          <div v-for="c in CHANNELS" :key="c" class="scope-line">
            <div class="scope-text">
              <div>{{ t("monitor.scope." + c) }}</div>
              <div class="scope-state">
                {{ val(editing, "scope_" + c) ? t("monitor.scopeOn") : t("monitor.scopeOff") }}
                <span v-if="!isExplicit(editing, c)" class="scope-derived">· {{ t("profile.followDefault") }}</span>
              </div>
            </div>
            <n-space :size="6" align="center">
              <n-switch size="small" :value="Boolean(val(editing, 'scope_' + c))"
                @update:value="(v: boolean) => setVal(editing!, 'scope_' + c, v)" />
              <n-button
                v-if="c === 'scheduled'"
                size="tiny"
                tertiary
                @click="resetScope(editing.id, c)"
              >{{ t("profile.followDefault") }}</n-button>
            </n-space>
          </div>
          <div class="drawer-hint">{{ t("models.scopeHint") }}</div>

          <n-divider>{{ t("profile.fNote") }}</n-divider>
          <n-input
            :value="val(editing, 'note')"
            @update:value="(v: string) => setVal(editing!, 'note', v)"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 4 }"
            maxlength="200"
            show-count
          />
        </n-form>
        <template #footer>
          <n-space justify="end">
            <n-button size="small" tertiary @click="drawerOpen = false">{{ t("common.close") }}</n-button>
            <n-button size="small" type="primary" @click="drawerOpen = false">
              {{ t("profile.keepEditing") }}
            </n-button>
          </n-space>
        </template>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NAlert, NButton, NCard, NCode, NDataTable, NDatePicker, NDescriptions, NDescriptionsItem, NDivider,
  NDrawer, NDrawerContent, NEmpty, NForm, NFormItem, NInput, NInputNumber, NModal, NProgress,
  NSelect, NSpace, NSwitch, NTag, NText, NTooltip, useMessage,
} from "naive-ui";
import type { DataTableColumns } from "naive-ui";

import {
  apiGet, apiPost, apiGetHealth, apiSetProfile, apiSetScope, apiSetVendor, startTestAllStream,
  fmtMs, fmtMoney,
  type HealthItem, type HealthResponse, type TestResult, type VendorProfile,
} from "../api";

const { t } = useI18n();
const message = useMessage();

const CHANNELS = ["manual", "scheduled", "command"] as const;
type Channel = (typeof CHANNELS)[number];

// 兜底枚举；后端 /panel/health 的 enums.billing_type 才是权威，
// 加新计费类型时只改后端的话这里不会漏（billingOptions 优先用后端那份）。
const BILLING = ["unknown", "free", "temp_free", "trial", "paid_overage", "paid",
                 "subscription", "per_request"];
const ROLES = ["unknown", "primary", "backup", "fallback", "dedicated", "watch", "retired"];
// 单价一律「每百万 token」，只有按次单价是每次 —— 单位必须分开标，
// 否则按次的人会把 0.002 填进 token 价里，算出来的花费差六个数量级。
// precision 必须给到 **8**（v1.4.2）：n-input-number 的 precision 会把值**静默四舍五入**，
// 以前写 4，填 0.00025 这类便宜模型的缓存价/按量价会被悄悄改成 0.0003 ——
// 界面上毫无提示，花费却已经算错了；后端是 REAL 存原值，不设这个限制，问题只在前端。
const PRICE_FIELDS = [
  { key: "price_input_per_m", label: (tr: any) => tr("profile.fPriceInput"),
    step: 0.01, precision: 8, unitKey: "profile.perMillion" },
  { key: "price_output_per_m", label: (tr: any) => tr("profile.fPriceOutput"),
    step: 0.01, precision: 8, unitKey: "profile.perMillion" },
  { key: "price_cached_per_m", label: (tr: any) => tr("profile.fPriceCached"),
    step: 0.001, precision: 8, unitKey: "profile.perMillion" },
  { key: "price_per_call", label: (tr: any) => tr("profile.fPricePerCall"),
    step: 0.0001, precision: 8, unitKey: "profile.perCall" },
] as const;

const data = ref<HealthResponse | null>(null);
const loading = ref(false);
const refreshing = ref(false);
const saving = ref(false);
const keyword = ref("");
const sortKey = ref<"status" | "latency" | "name">("status");

const results = reactive<Record<string, TestResult>>({});
const pending = reactive<Record<string, boolean>>({});
const testing = ref(false);
const progress = reactive({ current: 0, total: 0, percent: 0 });
const stats = ref<{ ok_count: number; fail_count: number; skip_count: number } | null>(null);

const detailVisible = ref(false);
const detailRow = ref<TestResult | null>(null);
const drawerOpen = ref(false);
const editing = ref<HealthItem | null>(null);

/** 草稿表：provider_id -> 被改动的字段。未保存前不落库。 */
const drafts = reactive<Record<string, any>>({});
/** 分组（供应商）级草稿：分组名 -> 被改动的字段。与行草稿共用同一条保存栏。 */
const vendorDrafts = reactive<Record<string, any>>({});

const vendors = computed<Record<string, VendorProfile>>(() => data.value?.vendors || {});

function vendorDraft(name: string) {
  if (!vendorDrafts[name]) vendorDrafts[name] = {};
  return vendorDrafts[name];
}

function vendorVal(name: string, key: string): any {
  const d = vendorDrafts[name] || {};
  if (key in d) return d[key];
  return (vendors.value[name] as any)?.[key] ?? null;
}

function setVendorVal(name: string, key: string, v: any) {
  vendorDraft(name)[key] = v;
}

/** 抽屉里实时回显「这个模型最终按几倍计」，让「留空跟随分组」不是句空话。 */
function effectiveMultiplier(it: HealthItem): number {
  const own = val(it, "rate_multiplier");
  if (own !== null && own !== undefined && own !== "") return Number(own);
  const group = vendorVal(it.name || "", "rate_multiplier");
  if (group !== null && group !== undefined && group !== "") return Number(group);
  return 1;
}

/** 今日调用数按供应商汇总：llm_calls 逐次埋点（与实时监测同源，含 AstrBot 后台
    的「提供商测试」与 WebChat；不含检测）。成功 / 失败单独一支，见 callsSplit。 */
function limitText(name: string): string {
  const v = vendors.value[name];
  const used = Number(v?.calls_today ?? 0);
  const limit = vendorVal(name, "daily_call_limit");
  return limit ? t("models.callsOfLimit", { used, limit }) : t("models.costToday") + " " + used;
}

/** 成功 / 失败拆分：之前只有总数，出问题时看不出是调用多了还是失败多了。 */
function callsSplit(name: string): string {
  const v = vendors.value[name] || {};
  return t("models.callsSplit", {
    ok: Number(v?.calls_ok ?? 0),
    fail: Number(v?.calls_fail ?? 0),
  });
}

function limitClass(name: string): string {
  const limit = Number(vendorVal(name, "daily_call_limit") || 0);
  if (!limit) return "limit-none";
  const ratio = Number(vendors.value[name]?.calls_today || 0) / limit;
  if (ratio >= 1) return "limit-over";
  if (ratio >= 0.8) return "limit-near";
  return "limit-ok";
}

const items = computed<HealthItem[]>(() => data.value?.items ?? []);

const shown = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  if (!kw) return items.value;
  return items.value.filter((it) =>
    `${it.display_model} ${it.model} ${it.name} ${it.id} ${it.billing.note || ""}`
      .toLowerCase().includes(kw));
});

// ---------------------------------------------------------------- 草稿读写
function draft(id: string) {
  if (!drafts[id]) drafts[id] = {};
  return drafts[id];
}

/** 定时通道的「跟随默认」由计费类型推导，与后端 scheduled_default_for 同一套枚举。 */
function derived(it: HealthItem, channel: Channel): boolean {
  if (channel !== "scheduled") return true;
  return (data.value?.enums?.scheduled_default_on || []).includes(it.billing.type);
}

/** 写回后端的字段名与后端返回的 billing 子键不同名（计费类型返回时叫 type）。 */
const BILLING_ALIAS: Record<string, string> = { billing_type: "type" };

function val(it: HealthItem, key: string): any {
  const d = drafts[it.id] || {};
  if (key.startsWith("scope_")) {
    const c = key.slice(6) as Channel;
    if (key in d) return d[key] === null ? derived(it, c) : d[key];
    return it.scope[c];
  }
  if (key in d) return d[key];
  return (it.billing as any)[BILLING_ALIAS[key] || key];
}

function setVal(it: HealthItem, key: string, v: any) {
  draft(it.id)[key] = v;
}

function isExplicit(it: HealthItem, channel: Channel) {
  return Boolean(it.scope.explicit?.[channel]);
}

function resetScope(id: string, channel: Channel) {
  draft(id)["scope_" + channel] = null;
}

const dirtyList = computed(() =>
  Object.keys(drafts).filter((id) => Object.keys(drafts[id]).length > 0));
const dirtyVendorList = computed(() =>
  Object.keys(vendorDrafts).filter((n) => Object.keys(vendorDrafts[n]).length > 0));
const dirtyCount = computed(() => dirtyList.value.length + dirtyVendorList.value.length);

function discard() {
  Object.keys(drafts).forEach((k) => delete drafts[k]);
  Object.keys(vendorDrafts).forEach((k) => delete vendorDrafts[k]);
}

// ---------------------------------------------------------------- 分组
type Group = { name: string; items: HealthItem[] };

const groups = computed<Group[]>(() => {
  const map = new Map<string, HealthItem[]>();
  for (const it of shown.value) {
    const key = it.name || t("profile.ungrouped");
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(it);
  }
  const out: Group[] = [];
  for (const [name, list] of map.entries()) {
    const sorted = [...list];
    if (sortKey.value === "name") {
      sorted.sort((a, b) => (a.model || a.id).localeCompare(b.model || b.id, "zh"));
    } else if (sortKey.value === "latency") {
      sorted.sort((a, b) => {
        const la = results[a.id]?.latency_ms ?? Number.MAX_SAFE_INTEGER;
        const lb = results[b.id]?.latency_ms ?? Number.MAX_SAFE_INTEGER;
        return la - lb;
      });
    } else {
      // 状态排序把失败与未检测顶到前面：这一页是拿来「找问题」的
      const score = (id: string) => {
        const r = results[id];
        if (!r) return 3;
        if (r.skipped) return 4;
        return r.ok ? 1 : 2;
      };
      sorted.sort((a, b) => score(a.id) - score(b.id));
    }
    out.push({ name, items: sorted });
  }
  return out.sort((a, b) => b.items.length - a.items.length || a.name.localeCompare(b.name, "zh"));
});

// ---------------------------------------------------------------- 组级一键设置
function opts(values: string[], ns: string) {
  return values.map((v) => ({ label: t(`${ns}.${v}`), value: v }));
}
const billingOptions = computed(() =>
  opts(data.value?.enums?.billing_type?.length ? data.value.enums.billing_type : BILLING,
       "monitor.billing"));
const roleOptions = computed(() => opts(ROLES, "monitor.role"));
const channelOptions = computed(() =>
  opts(["unknown", "official", "aggregator", "reseller", "self_hosted"], "monitor.channel"));
const streamOptions = computed(() => opts(["unknown", "true", "false"], "monitor.stream"));
const probeModeOptions = computed(() => opts(["non_stream", "stream", "both"], "profile.probeMode"));
const currencyOptions = computed(() => [
  { label: t("profile.curNone"), value: "" },
  { label: "CNY ¥", value: "CNY" },
  { label: "USD $", value: "USD" },
]);
const sortOptions = computed(() => [
  { label: t("models.sortStatus"), value: "status" },
  { label: t("detect.sort.latency"), value: "latency" },
  { label: t("detect.sort.name"), value: "name" },
]);

function applyGroupBilling(g: Group, billing: string) {
  if (!billing) return;
  g.items.forEach((it) => setVal(it, "billing_type", billing));
  message.success(t("profile.batchDone", {
    vendor: g.name, billing: t("monitor.billing." + billing), n: g.items.length,
  }));
}

function channelTally(g: Group, c: Channel) {
  const on = g.items.filter((it) => Boolean(val(it, "scope_" + c))).length;
  return { on, all: on === g.items.length, mixed: on > 0 && on < g.items.length };
}

/** 点一下整组翻转：全开→全关，其余（含半开）→全开。 */
function flipGroupChannel(g: Group, c: Channel) {
  const target = !channelTally(g, c).all;
  g.items.forEach((it) => setVal(it, "scope_" + c, target));
}

function setAllChannel(c: Channel, on: boolean) {
  shown.value.forEach((it) => setVal(it, "scope_" + c, on));
}

// ---------------------------------------------------------------- 检测
function latencyClass(ms: number): "success" | "warning" | "error" {
  if (ms < 1500) return "success";
  if (ms < 5000) return "warning";
  return "error";
}

function resultTag(r: TestResult) {
  if (r.skipped) return { type: "default" as const, label: t("detect.result.skipped") };
  if (r.ok) return { type: "success" as const, label: t("detect.result.ok") };
  return { type: "error" as const, label: t(`detect.result.${r.error_code || "unknown"}`) };
}

function formatTime(ts?: number): string {
  if (!ts) return "—";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return String(ts);
  }
}

function jsonText(row: TestResult | null): string {
  try {
    return JSON.stringify(row, null, 2);
  } catch {
    return String(row);
  }
}

function openDetail(it: HealthItem) {
  const r = results[it.id];
  if (!r) {
    message.info(t("detect.detail.noResult"));
    return;
  }
  detailRow.value = r;
  detailVisible.value = true;
}

const detailName = computed(() => {
  const id = detailRow.value?.id;
  const it = items.value.find((x) => x.id === id);
  return it ? (it.display_model || it.model || it.id) : id || "";
});

async function testOne(it: HealthItem) {
  pending[it.id] = true;
  try {
    // 单独检测会阻塞到 provider.test() 返回（最长 test_timeout + 5s），
    // 桥接层默认 6s 会误杀，这里给 180s 兜底
    const r = await apiPost<TestResult>("/panel/providers/test", { id: it.id }, 180000);
    results[it.id] = r;
  } catch (e) {
    results[it.id] = {
      id: it.id, name: it.name, model: it.model, ok: false, latency_ms: null,
      error_code: "unknown", error: String((e as Error).message || e), retry_count: 0,
    };
  } finally {
    pending[it.id] = false;
  }
}

/** 检测前先把草稿落库：屏幕上显示的开关状态必须就是后端真正用的那份，
 *  否则「刚把定时巡检打开就点检测」会按库里的旧值跳过该模型。 */
async function flushDrafts(): Promise<boolean> {
  if (!dirtyCount.value) return true;
  await saveAll();
  return !dirtyCount.value;
}

async function runStream(ids: string[] | null) {
  if (testing.value) return;
  if (!(await flushDrafts())) return;
  const skip = items.value
    .filter((it) => !Boolean(val(it, "scope_manual")))
    .map((it) => it.id);
  testing.value = true;
  stats.value = null;
  progress.current = 0;
  progress.total = 0;
  progress.percent = 0;
  const scope = ids ? new Set(ids) : null;
  for (const k of Object.keys(pending)) pending[k] = false;
  try {
    await new Promise<void>((resolve, reject) => {
      startTestAllStream({ skip, ids: ids || undefined }, {
        onStart: (e) => {
          progress.total = e.total || 0;
          for (const it of items.value) {
            if ((!scope || scope.has(it.id)) && !skip.includes(it.id)) pending[it.id] = true;
          }
        },
        onItem: (e) => {
          results[e.item.id] = e.item;
          pending[e.item.id] = false;
          progress.current = Math.min(e.index || progress.current, e.total || progress.total);
          progress.percent = progress.total
            ? Math.round((progress.current / progress.total) * 100) : 0;
        },
        onDone: (e) => {
          stats.value = { ok_count: e.ok_count, fail_count: e.fail_count, skip_count: e.skip_count };
          progress.current = progress.total || e.total || 0;
          progress.percent = 100;
          resolve();
        },
        onError: (msg) => reject(new Error(msg)),
      });
    });
  } catch (e) {
    const msg = String((e as Error)?.message || e);
    if (msg.includes("已有检测任务")) message.warning(t("detect.testingBusyHint"));
    else message.error(t("detect.testingFail", { msg }));
  } finally {
    testing.value = false;
    progress.current = 0;
    progress.total = 0;
    progress.percent = 0;
    for (const k of Object.keys(pending)) pending[k] = false;
  }
}

function testAll() {
  return runStream(null);
}

function testGroup(g: Group) {
  return runStream(g.items.map((it) => it.id));
}

// ---------------------------------------------------------------- 保存
async function saveAll() {
  const ids = dirtyList.value;
  const vnames = dirtyVendorList.value;
  if (!ids.length && !vnames.length) return;
  saving.value = true;
  let ok = 0;
  const errors: string[] = [];
  for (const id of ids) {
    const d = drafts[id] || {};
    const patch: Record<string, any> = {};
    Object.keys(d).forEach((k) => {
      if (!k.startsWith("scope_")) patch[k] = d[k];
    });
    try {
      if (Object.keys(patch).length) {
        const r = await apiSetProfile(id, patch);
        if (r && r.ok === false) throw new Error(r.error || t("profile.saveFailed"));
      }
      for (const c of CHANNELS) {
        const key = "scope_" + c;
        if (!(key in d)) continue;
        const r = await apiSetScope(id, c, d[key] === null ? null : Boolean(d[key]));
        if (r && r.ok === false) throw new Error(r.error || t("profile.saveFailed"));
      }
      delete drafts[id];
      ok += 1;
    } catch (e: any) {
      errors.push(`${id}: ${e?.message || e}`);
    }
  }
  for (const name of vnames) {
    const patch = { ...vendorDrafts[name] };
    try {
      const r = await apiSetVendor(name, patch);
      if (r && r.ok === false) throw new Error(r.error || t("profile.saveFailed"));
      delete vendorDrafts[name];
      ok += 1;
    } catch (e: any) {
      errors.push(`${name}: ${e?.message || e}`);
    }
  }
  saving.value = false;
  if (errors.length) message.error(t("profile.savedWithErrors", { n: errors.length, why: errors[0] }));
  else message.success(t("profile.saved", { n: ok }));
  await load(true);
}

// ---------------------------------------------------------------- 加载
function epochToStr(v: number | null): string | null {
  if (!v) return null;
  const d = new Date(v * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function strToEpoch(s: string | null): number | null {
  if (!s) return null;
  const ms = new Date(s + "T00:00:00").getTime();
  return Number.isNaN(ms) ? null : Math.floor(ms / 1000);
}

function openDrawer(it: HealthItem) {
  editing.value = it;
  drawerOpen.value = true;
}

const drawerTitle = computed(() =>
  (editing.value ? t("profile.drawerTitle") + " · " + editing.value.display_model : ""));

async function load(keepDrafts = false) {
  if (refreshing.value) return;
  refreshing.value = true;
  try {
    const next = await apiGetHealth(7);
    const snapshot: Record<string, any> = {};
    Object.keys(drafts).forEach((k) => (snapshot[k] = { ...drafts[k] }));
    const vsnap: Record<string, any> = {};
    Object.keys(vendorDrafts).forEach((k) => (vsnap[k] = { ...vendorDrafts[k] }));
    data.value = next;
    Object.keys(drafts).forEach((k) => delete drafts[k]);
    Object.keys(vendorDrafts).forEach((k) => delete vendorDrafts[k]);
    if (keepDrafts) {
      Object.keys(snapshot).forEach((k) => (drafts[k] = snapshot[k]));
      Object.keys(vsnap).forEach((k) => (vendorDrafts[k] = vsnap[k]));
    }
    if (editing.value) {
      editing.value = next.items.find((x) => x.id === editing.value?.id) || null;
    }
    await loadResults();
  } catch (e: any) {
    message.error(e?.message || String(e));
  } finally {
    refreshing.value = false;
  }
}

async function loadResults() {
  try {
    const r = await apiGet<{ items: Record<string, TestResult> }>("/panel/providers/results");
    for (const k of Object.keys(r.items || {})) results[k] = r.items[k];
  } catch (e) {
    console.warn("拉取最新检测结果失败", e);
  }
}

// ---------------------------------------------------------------- 表格列
const columns = computed<DataTableColumns<HealthItem>>(() => [
  {
    title: t("models.colModel"), key: "model", minWidth: 200,
    render: (it) => {
      const note = String(val(it, "note") || "").trim();
      return h("div", { class: "mm-cell" }, [
        h("div", { class: "mm-name" }, [
          it.model || it.display_model || it.id,
          it.is_default
            ? h(NTag, { size: "tiny", type: "warning", round: true, bordered: false, style: "margin-left:6px" },
                () => t("monitor.defaultTag"))
            : null,
        ]),
        // 第二行只放备注：供应商名已经在组头写过了，这里再写一遍是纯噪音
        note ? h("div", { class: "mm-note" }, note) : null,
      ]);
    },
  },
  {
    title: t("detect.colStatus"), key: "status", width: 84,
    render: (it) => {
      const r = results[it.id];
      if (r) {
        const tag = resultTag(r);
        return h(NTag, { type: tag.type, size: "small", round: true }, () => tag.label);
      }
      if (pending[it.id]) return h(NText, { depth: 3 }, () => t("detect.statusPending"));
      return h(NText, { depth: 3 }, () => t("detect.statusUnchecked"));
    },
  },
  {
    title: t("detect.colLatency"), key: "latency", width: 78,
    render: (it) => {
      const ms = results[it.id]?.latency_ms;
      if (ms == null) return h(NText, { depth: 3 }, () => "—");
      return h(NTag, { type: latencyClass(ms), size: "small", bordered: false }, () => fmtMs(ms));
    },
  },
  {
    title: t("detect.colCheckedAt"), key: "checked_at", width: 138,
    render: (it) => {
      const ts = results[it.id]?.checked_at;
      if (!ts) return h(NText, { depth: 3 }, () => "—");
      return h(NText, { depth: 3, style: "font-size:12px;white-space:nowrap" }, () => formatTime(ts));
    },
  },
  {
    title: t("monitor.colBilling"), key: "billing_type", width: 124,
    render: (it) => h(NSelect, {
      size: "small", value: val(it, "billing_type"), options: billingOptions.value,
      "onUpdate:value": (v: string) => setVal(it, "billing_type", v),
    }),
  },
  {
    title: t("profile.colFreeUntil"), key: "free_until", width: 134,
    render: (it) => h("div", { class: val(it, "billing_type") === "temp_free" ? "fu-on" : "fu-off" }, [
      h(NDatePicker, {
        size: "small", type: "date", value: val(it, "free_until"), clearable: true,
        "formatted-value": epochToStr(val(it, "free_until")),
        "value-format": "yyyy-MM-dd",
        style: "width:118px",
        "onUpdate:formatted-value": (v: string | null) => setVal(it, "free_until", strToEpoch(v)),
      }),
    ]),
  },
  {
    title: t("models.colCost"), key: "cost", width: 104, align: "right" as const,
    render: (it) => {
      const c = it.cost || ({} as any);
      const week = c.week;
      // 没填单价时是「估不出来」，显示 – 而不是 ¥0.00 —— 后者会被读成「真没花钱」
      const main = week === null || week === undefined
        ? h(NText, { depth: 3 }, () => t("models.costNone"))
        : h("span", { class: "cost-num" }, fmtMoney(week, c.currency));
      const tag = h("div", { class: "cost-sub" },
        c.multiplier && c.multiplier !== 1
          ? t(c.multiplier_from_group ? "models.groupMultiplierTag" : "models.multiplierTag",
              { n: c.multiplier })
          : t("models.costToday") + " " + fmtMoney(c.today ?? null, c.currency));
      return h(NTooltip, { trigger: "hover" }, {
        default: () => t("models.costWeekTip"),
        trigger: () => h("div", { class: "cost-cell" }, [main, tag]),
      });
    },
  },
  ...CHANNELS.map<DataTableColumns<HealthItem>[number]>((c) => ({
    // 三通道各占一列并把列名写全：合并成一列「三通道」后要靠 tooltip 猜哪个开关是谁
    title: t("monitor.scope." + c), key: "scope_" + c, width: 76, align: "center",
    render: (it) => {
      const on = Boolean(val(it, "scope_" + c));
      const follow = !isExplicit(it, c) && c === "scheduled";
      const sw = h(NSwitch, {
        size: "small", value: on,
        "onUpdate:value": (v: boolean) => setVal(it, "scope_" + c, v),
      });
      if (c !== "scheduled") return sw;
      return h(NTooltip, { trigger: "hover" }, {
        default: () => t("models.scheduledTip") + (follow ? t("profile.derivedTip") : ""),
        trigger: () => h("span", { class: follow ? "sc-follow" : "" }, [sw]),
      });
    },
  })),
  {
    title: t("detect.colAction"), key: "action", width: 156,
    render: (it) => h(NSpace, { size: 6, wrap: false }, {
      default: () => [
        h(NButton, {
          size: "tiny", type: "primary", ghost: true,
          loading: !!pending[it.id], disabled: testing.value,
          onClick: () => testOne(it),
        }, () => (pending[it.id] ? t("detect.btnTesting") : t("models.btnTest"))),
        h(NButton, {
          size: "tiny", tertiary: true, disabled: !results[it.id],
          onClick: () => openDetail(it),
        }, () => t("models.btnDetail")),
        h(NButton, { size: "tiny", tertiary: true, onClick: () => openDrawer(it) },
          () => t("profile.more")),
      ],
    }),
  },
]);

onMounted(async () => {
  loading.value = true;
  try {
    data.value = await apiGetHealth(7);
  } catch (e: any) {
    message.error(e?.message || String(e));
    data.value = { items: [], counts: {}, days: 7, live_available: false, truncated: false, samples: 0, alerts: [], muted_count: 0 };
  } finally {
    loading.value = false;
  }
  await loadResults();
  document.addEventListener("visibilitychange", onVisibility);
});

function onVisibility() {
  if (document.visibilityState === "visible" && !testing.value) loadResults();
}
</script>

<style scoped>
.models { display: flex; flex-direction: column; gap: 12px; }
.m-note { font-size: 12px; line-height: 1.6; opacity: .9; }
.m-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
.m-hint { font-size: 12px; }
.m-banner { margin-top: 2px; }
.m-empty { display: block; padding: 24px; text-align: center; opacity: .6; }
.m-stats :deep(.n-card__content) { padding: 10px 14px; }
.group-block { margin-bottom: 10px; }
.group-head {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 7px 10px; border-radius: 8px 8px 0 0;
  background: linear-gradient(135deg, rgba(255, 126, 178, .12), rgba(150, 110, 235, .12));
  border-left: 3px solid var(--accent);
}
.group-name { font-size: 14px; font-weight: 700; color: var(--accent-2); }
.group-count { font-size: 12px; opacity: .6; margin-right: 6px; }
.group-spacer { flex: 1 1 auto; }
.chip-mixed { border-style: dashed !important; }
.limit-chip {
  font-size: 11px; padding: 1px 8px; border-radius: 9px;
  background: rgba(148, 163, 184, .14); color: var(--muted); white-space: nowrap;
}
/* 成功 / 失败拆分支：文案里的 ✓ / ✗ 由 locale 给（伪元素插符号会落到句尾，位置不对） */
.calls-split { color: var(--muted); }
.limit-near { background: rgba(229, 154, 43, .18); color: var(--warn); }
.limit-over { background: rgba(239, 77, 104, .18); color: var(--err); font-weight: 700; }
.limit-none { opacity: .6; }
:deep(.cost-cell) { display: flex; flex-direction: column; align-items: flex-end; gap: 1px; }
:deep(.cost-num) { font-size: 13px; font-weight: 700; font-variant-numeric: tabular-nums; }
:deep(.cost-sub) { font-size: 10px; opacity: .55; }
.m-savebar {
  position: sticky; bottom: 0; z-index: 5;
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 12px; border-radius: 10px;
  background: rgba(139, 92, 246, .12); border: 1px solid rgba(139, 92, 246, .3);
}
.save-hint { font-size: 13px; font-weight: 600; }
.m-rawlabel { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
:deep(.mm-cell) { display: flex; flex-direction: column; gap: 2px; }
:deep(.mm-name) { font-size: 14px; font-weight: 700; line-height: 1.4; word-break: break-all; }
:deep(.mm-note) { font-size: 11px; opacity: .62; word-break: break-all; line-height: 1.4; }
:deep(.fu-off) { opacity: .4; }
:deep(.sc-follow) { outline: 1px dashed rgba(148, 163, 184, .6); outline-offset: 1px; border-radius: 12px; }
:deep(.drawer-sub) { font-size: 12px; opacity: .6; margin-bottom: 10px; word-break: break-all; }
:deep(.drawer-hint) { font-size: 11px; opacity: .6; margin: -6px 0 8px; line-height: 1.6; }
:deep(.scope-line) { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; gap: 12px; }
:deep(.scope-text) { font-size: 13px; }
:deep(.scope-state) { font-size: 11px; opacity: .6; }
:deep(.scope-derived) { color: #a78bfa; }
</style>

<style>
/* 全局覆盖：n-modal 的 .n-modal-scroll-content 有 min-height:100%，
   会让弹窗在内容很少时也撑满 viewport、关闭按钮被顶出可视区。
   这里限高并让内容区自己滚，关闭按钮才始终点得到。 */
.n-modal-scroll-content {
  min-height: auto !important;
}
.n-modal {
  max-height: calc(100vh - 64px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.n-modal .n-card {
  max-height: calc(100vh - 64px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.n-modal .n-card__content {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
}
</style>
