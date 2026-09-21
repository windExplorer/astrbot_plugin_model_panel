<template>
  <div class="profile">
    <n-alert type="info" :show-icon="false" class="p-note">
      {{ t('profile.scheduledDefaultNote') }}
    </n-alert>

    <div class="p-toolbar">
      <n-space align="center" :size="10" wrap>
        <n-select
          v-model:value="batchVendor"
          size="small"
          :options="vendorOptions"
          :placeholder="t('profile.batchVendor')"
          style="width: 190px"
          filterable
        />
        <n-select
          v-model:value="batchBilling"
          size="small"
          :options="billingOptions"
          :placeholder="t('profile.batchBilling')"
          style="width: 160px"
        />
        <n-button size="small" tertiary :disabled="!batchVendor || !batchBilling" @click="applyBatch">
          {{ t('profile.batchApply') }}
        </n-button>
        <n-divider vertical />
        <n-input
          v-model:value="keyword"
          size="small"
          :placeholder="t('profile.searchPh')"
          clearable
          style="width: 170px"
        />
        <n-button size="small" quaternary :loading="loading" @click="load">
          {{ t('detect.btnRefresh') }}
        </n-button>
      </n-space>
    </div>

    <n-data-table
      :columns="columns"
      :data="shown"
      :loading="loading"
      :row-key="(r: HealthItem) => r.id"
      size="small"
      striped
      :scroll-x="1120"
      :pagination="pagination"
    >
      <template #empty>
        <span class="p-empty">{{ t('profile.empty') }}</span>
      </template>
    </n-data-table>

    <div v-if="dirtyCount" class="p-savebar">
      <span class="save-hint">{{ t('profile.dirtyCount', { n: dirtyCount }) }}</span>
      <n-space :size="8">
        <n-button size="small" tertiary @click="discard">{{ t('profile.discard') }}</n-button>
        <n-button size="small" type="primary" :loading="saving" @click="saveAll">
          {{ t('profile.saveAll') }}
        </n-button>
      </n-space>
    </div>

    <n-drawer v-model:show="drawerOpen" :width="420" placement="right">
      <n-drawer-content :title="drawerTitle" closable>
        <n-form v-if="editing" label-placement="top" size="small">
          <div class="drawer-sub">{{ editing.display_model }} · {{ editing.id }}</div>

          <n-form-item :label="t('profile.fChannel')">
            <n-select :value="val(editing, 'channel_kind')" :options="channelOptions"
              @update:value="(v: string) => setVal(editing!, 'channel_kind', v)" />
          </n-form-item>
          <n-form-item :label="t('profile.fStream')">
            <n-select :value="val(editing, 'supports_streaming')" :options="streamOptions"
              @update:value="(v: string) => setVal(editing!, 'supports_streaming', v)" />
          </n-form-item>
          <n-form-item :label="t('profile.fProbeMode')">
            <span class="drawer-hint">
              {{ t('profile.probeMode.' + (val(editing, 'probe_mode') || 'non_stream')) }} · {{ t('profile.probeModeLocked') }}
            </span>
          </n-form-item>

          <n-divider>{{ t('profile.priceGroup') }}</n-divider>
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
              :step="0.5"
              clearable
              style="width: 100%"
            >
              <template #suffix>{{ t('profile.perMillion') }}</template>
            </n-input-number>
          </n-form-item>
          <div class="drawer-hint">{{ t('profile.priceHint') }}</div>

          <n-divider>{{ t('profile.scopeGroup') }}</n-divider>
          <div v-for="c in CHANNELS" :key="c" class="scope-line">
            <div class="scope-text">
              <div>{{ t('monitor.scope.' + c) }}</div>
              <div class="scope-state">
                {{ val(editing, 'scope_' + c) ? t('monitor.scopeOn') : t('monitor.scopeOff') }}
                <span v-if="!isExplicit(editing, c)" class="scope-derived">· {{ t('profile.followDefault') }}</span>
              </div>
            </div>
            <n-space :size="6" align="center">
              <n-switch size="small" :value="Boolean(val(editing, 'scope_' + c))"
                @update:value="(v: boolean) => setVal(editing!, 'scope_' + c, v)" />
              <n-button
                v-if="isExplicit(editing, c)"
                size="tiny"
                tertiary
                @click="resetScope(editing.id, c)"
              >{{ t('profile.followDefault') }}</n-button>
            </n-space>
          </div>

          <n-divider>{{ t('profile.fNote') }}</n-divider>
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
            <n-button size="small" tertiary @click="drawerOpen = false">{{ t('common.close') }}</n-button>
            <n-button size="small" type="primary" @click="drawerOpen = false">{{ t('profile.keepEditing') }}</n-button>
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
  NAlert, NButton, NDataTable, NDatePicker, NDivider, NDrawer, NDrawerContent, NForm, NFormItem,
  NInput, NInputNumber, NSelect, NSpace, NSwitch, NTooltip, useMessage,
} from "naive-ui";
import type { DataTableColumns } from "naive-ui";

import {
  apiGetHealth, apiSetProfile, apiSetScope,
  type HealthItem, type HealthResponse,
} from "../api";

const { t } = useI18n();
const message = useMessage();

const BILLING = ["unknown", "free", "temp_free", "trial", "paid_overage", "paid", "subscription"];
const ROLES = ["unknown", "primary", "backup", "fallback", "dedicated", "watch", "retired"];
const CHANNELS = ["manual", "scheduled", "command"] as const;
const PRICE_FIELDS = [
  { key: "price_input_per_m", label: (tr: any) => tr("profile.fPriceInput") },
  { key: "price_output_per_m", label: (tr: any) => tr("profile.fPriceOutput") },
  { key: "price_cached_per_m", label: (tr: any) => tr("profile.fPriceCached") },
] as const;

const data = ref<HealthResponse | null>(null);
const loading = ref(false);
const saving = ref(false);
const keyword = ref("");
const batchVendor = ref<string | null>(null);
const batchBilling = ref<string | null>(null);
const drawerOpen = ref(false);
const editing = ref<HealthItem | null>(null);
/** 草稿表：provider_id -> 被改动的字段。未保存前不落库。 */
const drafts = reactive<Record<string, any>>({});
const pagination = { pageSize: 20, showSizePicker: true, pageSizes: [20, 50, 100] };

const items = computed<HealthItem[]>(() => data.value?.items ?? []);

const shown = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  if (!kw) return items.value;
  return items.value.filter((it) =>
    `${it.display_model} ${it.model} ${it.name} ${it.id}`.toLowerCase().includes(kw));
});

const vendorOptions = computed(() => {
  const set = new Map<string, number>();
  items.value.forEach((it) => set.set(it.name || it.id, (set.get(it.name || it.id) || 0) + 1));
  return Array.from(set.entries())
    .sort((a, b) => b[1] - a[1])
    .map(([name, n]) => ({ label: `${name} (${n})`, value: name }));
});

function opts(values: string[], ns: string) {
  return values.map((v) => ({ label: t(`${ns}.${v}`), value: v }));
}
const billingOptions = computed(() => opts(BILLING, "monitor.billing"));
const roleOptions = computed(() => opts(ROLES, "monitor.role"));
const channelOptions = computed(() => opts(["unknown", "official", "aggregator", "reseller", "self_hosted"], "monitor.channel"));
const streamOptions = computed(() => opts(["unknown", "true", "false"], "monitor.stream"));
const currencyOptions = computed(() => [
  { label: t("profile.curNone"), value: "" },
  { label: "CNY ¥", value: "CNY" },
  { label: "USD $", value: "USD" },
]);

function draft(id: string) {
  if (!drafts[id]) drafts[id] = {};
  return drafts[id];
}

/** 某通道的「跟随默认」值。后端把 NULL 存成跟随，前端要显示同一个语义。 */
function derived(it: HealthItem, channel: string): boolean {
  if (channel === "scheduled") {
    return (data.value?.enums?.scheduled_default_on || []).includes(it.billing.type);
  }
  return true;
}

/** 写回后端的字段名和后端返回的 billing 子键不完全同名（计费类型返回时叫 type），
 *  这里做一次映射；漏了的话那一列会永远显示空值。 */
const BILLING_ALIAS: Record<string, string> = { billing_type: "type" };

/** 读值优先取草稿，让「改了立刻在表格里看得到」而不用等保存。草稿为 null 表示跟随默认。 */
function val(it: HealthItem, key: string): any {
  const d = drafts[it.id] || {};
  if (key.startsWith("scope_")) {
    const c = key.slice(6);
    if (key in d) return d[key] === null ? derived(it, c) : d[key];
    return it.scope[c as (typeof CHANNELS)[number]];
  }
  if (key in d) return d[key];
  const bk = BILLING_ALIAS[key] || key;
  return (it.billing as any)[bk];
}

function setVal(it: HealthItem, key: string, v: any) {
  draft(it.id)[key] = v;
}

function isExplicit(it: HealthItem, channel: string) {
  return Boolean(it.scope.explicit?.[channel as (typeof CHANNELS)[number]]);
}

function resetScope(id: string, channel: string) {
  // 存 null：保存时原样发给后端写回 NULL 列，显示时按计费类型推导，两边语义一致
  draft(id)["scope_" + channel] = null;
}

const dirtyList = computed(() =>
  Object.keys(drafts).filter((id) => Object.keys(drafts[id]).length > 0));
const dirtyCount = computed(() => dirtyList.value.length);

function applyBatch() {
  const vendor = batchVendor.value;
  const billing = batchBilling.value;
  if (!vendor || !billing) return;
  let n = 0;
  items.value.forEach((it) => {
    if ((it.name || it.id) !== vendor) return;
    setVal(it, "billing_type", billing);
    n += 1;
  });
  message.success(t("profile.batchDone", { vendor, billing: t("monitor.billing." + billing), n }));
  batchBilling.value = null;
}

function discard() {
  Object.keys(drafts).forEach((k) => delete drafts[k]);
}

async function saveAll() {
  const ids = dirtyList.value;
  if (!ids.length) return;
  saving.value = true;
  let ok = 0;
  const errors: string[] = [];
  for (const id of ids) {
    const d = drafts[id] || {};
    const patch: Record<string, any> = {};
    Object.keys(d).forEach((k) => {
      if (k.startsWith("scope_")) return;
      patch[k] = d[k];
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
  saving.value = false;
  if (errors.length) message.error(t("profile.savedWithErrors", { n: errors.length, why: errors[0] }));
  else message.success(t("profile.saved", { n: ok }));
  await load(true);
}

async function load(keepDrafts = false) {
  loading.value = true;
  try {
    const next = await apiGetHealth(7);
    const snapshot: Record<string, any> = {};
    Object.keys(drafts).forEach((k) => (snapshot[k] = { ...drafts[k] }));
    data.value = next;
    Object.keys(drafts).forEach((k) => delete drafts[k]);
    if (keepDrafts) Object.keys(snapshot).forEach((k) => (drafts[k] = snapshot[k]));
    if (editing.value) {
      editing.value = next.items.find((x) => x.id === editing.value?.id) || null;
    }
  } catch (e: any) {
    message.error(e?.message || String(e));
  } finally {
    loading.value = false;
  }
}

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

const drawerTitle = computed(() => (editing.value ? t("profile.drawerTitle") + " · " + editing.value.display_model : ""));

const columns = computed<DataTableColumns<HealthItem>>(() => [
  {
    title: t("profile.colModel"), key: "model", minWidth: 220,
    render: (it) => h("div", { class: "pm-cell" }, [
      h("div", { class: "pm-name" }, it.display_model || it.model || it.id),
      h("div", { class: "pm-sub" }, [
        it.is_default ? t("monitor.defaultTag") : null,
        it.is_default ? " · " : null,
        it.name || null,
      ].filter(Boolean).join("")),
    ]),
  },
  {
    title: t("monitor.colBilling"), key: "billing_type", width: 150,
    render: (it) => h(NSelect, {
      size: "small", value: val(it, "billing_type"), options: billingOptions.value,
      "onUpdate:value": (v: string) => setVal(it, "billing_type", v),
    }),
  },
  {
    title: t("profile.colFreeUntil"), key: "free_until", width: 148,
    render: (it) => h("div", { class: val(it, "billing_type") === "temp_free" ? "fu-on" : "fu-off" }, [
      h(NDatePicker, {
        size: "small", type: "date", value: val(it, "free_until"), clearable: true,
        "formatted-value": epochToStr(val(it, "free_until")),
        "value-format": "yyyy-MM-dd",
        style: "width: 128px",
        "onUpdate:formatted-value": (v: string | null) => setVal(it, "free_until", strToEpoch(v)),
      }),
    ]),
  },
  {
    title: t("monitor.dRole"), key: "role", width: 116,
    render: (it) => h(NSelect, {
      size: "small", value: val(it, "role"), options: roleOptions.value,
      "onUpdate:value": (v: string) => setVal(it, "role", v),
    }),
  },
  {
    title: t("profile.colScope"), key: "scope", width: 150,
    render: (it) => h("div", { class: "scope-cells" }, CHANNELS.map((c) => {
      const v = val(it, "scope_" + c);
      const derived = !isExplicit(it, c);
      return h(NTooltip, { trigger: "hover" }, {
        default: () => t("monitor.scope." + c) + (derived ? t("profile.derivedTip") : ""),
        trigger: () => h("span", { class: ["sc", v ? "sc-on" : "sc-off", derived ? "sc-derived" : ""] }, [
          h(NSwitch, {
            size: "small", value: Boolean(v),
            "onUpdate:value": (nv: boolean) => setVal(it, "scope_" + c, nv),
          }),
        ]),
      });
    })),
  },
  {
    title: t("profile.colMore"), key: "more", width: 84, align: "center" as const,
    render: (it) => {
      const filled = [it.billing.channel_kind !== "unknown", Boolean(it.billing.currency),
        Boolean(it.billing.note), it.billing.supports_streaming !== "unknown"].filter(Boolean).length;
      return h(NButton, { size: "tiny", tertiary: true, onClick: () => openDrawer(it) },
        () => (filled ? `${t("profile.more")} ·${filled}` : t("profile.more")));
    },
  },
]);

onMounted(() => load());
</script>

<style scoped>
.profile { display: flex; flex-direction: column; gap: 12px; }
.p-note { font-size: 12px; line-height: 1.6; opacity: .9; }
.p-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
.p-empty { display: block; padding: 24px; text-align: center; opacity: .6; }
.p-savebar {
  position: sticky; bottom: 0; z-index: 5;
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 12px; border-radius: 10px;
  background: rgba(139, 92, 246, .12); border: 1px solid rgba(139, 92, 246, .3);
}
.save-hint { font-size: 13px; font-weight: 600; }
:deep(.pm-cell) { display: flex; flex-direction: column; gap: 2px; }
:deep(.pm-name) { font-size: 13px; }
:deep(.pm-sub) { font-size: 11px; opacity: .55; }
:deep(.fu-off) { opacity: .4; }
:deep(.scope-cells) { display: flex; gap: 6px; align-items: center; }
:deep(.sc) { display: inline-flex; border-radius: 6px; padding: 0 2px; }
:deep(.sc-derived) { outline: 1px dashed rgba(148, 163, 184, .5); outline-offset: 1px; }
:deep(.drawer-sub) { font-size: 12px; opacity: .6; margin-bottom: 10px; word-break: break-all; }
:deep(.drawer-hint) { font-size: 11px; opacity: .6; margin: -6px 0 8px; line-height: 1.6; }
:deep(.scope-line) { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; gap: 12px; }
:deep(.scope-text) { font-size: 13px; }
:deep(.scope-state) { font-size: 11px; opacity: .6; }
:deep(.scope-derived) { color: #a78bfa; }
</style>
