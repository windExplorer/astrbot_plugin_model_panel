<template>
  <div class="companion-view">
    <n-card :title="t('companion.title')" size="small">
      <n-text v-if="loaded === false" depth="3">{{ t("companion.notLoaded") }}</n-text>
      <template v-else>
        <n-space align="center" wrap class="toolbar">
          <n-button type="primary" :loading="saving" @click="save">
            {{ saving ? t("companion.btnSaving") : t("companion.btnSave") }}
          </n-button>
          <n-button quaternary :loading="refreshing" @click="manualRefresh">
            {{ t("companion.btnRefresh") }}
          </n-button>
          <n-text v-if="configMode" depth="3">
            {{ t("companion.configMode", { mode: configMode || t("companion.configModeNone") }) }}
          </n-text>
          <n-text depth="3">
            {{ t("companion.configuredCount", { n: configuredCount, total: totalKeys }) }}
          </n-text>
          <n-text v-if="unconfiguredCount > 0" type="warning">
            {{ t("companion.unconfiguredHint", { n: unconfiguredCount }) }}
          </n-text>
        </n-space>

        <n-tabs type="line" class="mt-2">
          <!-- Tab1：精简配置（初心）—— 按模型名替换，不管主备位置 -->
          <n-tab-pane name="simple" :tab="t('companion.tabSimple')">
            <n-text depth="3" class="block">
              {{ t("companion.simpleExplain") }}
            </n-text>
            <n-empty
              v-if="!simpleRows.length"
              :description="t('companion.usedModelsEmpty')"
              class="empty"
            />
            <n-data-table
              v-else
              :columns="simpleColumns"
              :data="simpleRows"
              :pagination="false"
              size="small"
              :bordered="true"
              :single-line="false"
              :row-key="(row) => row.value"
              :scroll-x="900"
            />
          </n-tab-pane>

          <!-- Tab2：主次区分配置 —— 主模型 / 备用模型分开替换 -->
          <n-tab-pane name="advanced" :tab="t('companion.tabAdvanced')">
            <n-text depth="3" class="block">
              {{ t("companion.advancedExplain") }}
            </n-text>
            <n-empty
              v-if="!advRows.length"
              :description="t('companion.usedModelsEmpty')"
              class="empty"
            />
            <n-data-table
              v-else
              :columns="advColumns"
              :data="advRows"
              :pagination="false"
              size="small"
              :bordered="true"
              :single-line="false"
              :row-key="(row) => row.aggKey"
              :scroll-x="900"
            />
          </n-tab-pane>
        </n-tabs>

        <n-collapse class="mt-3" :default-expanded-names="[]">
          <n-collapse-item :title="unconfiguredTitle" name="unconfigured">
            <template #header-extra>
              <n-tag size="small" type="warning" round>{{ unconfiguredItems.length }}</n-tag>
            </template>
            <n-text depth="3" class="block">
              {{ t("companion.unconfiguredExplain") }}
            </n-text>
            <n-text depth="3" class="block">
              {{ t("companion.unconfiguredHowto") }}
            </n-text>
            <n-data-table
              :columns="unconfiguredColumns"
              :data="unconfiguredItems"
              :pagination="false"
              size="small"
              :bordered="false"
              :row-key="(row) => row.key"
              class="mt-2"
            />
          </n-collapse-item>
        </n-collapse>
      </template>
    </n-card>

    <!-- 模型使用详情弹窗：点"适用于"那个 tag 打开 -->
    <n-modal
      v-model:show="detailVisible"
      preset="card"
      :title="t('companion.detail.title')"
      style="max-width: 720px; width: calc(100vw - 48px)"
      :style="{ maxHeight: 'calc(100vh - 64px)' }"
      :scrollbar-props="{ trigger: 'none' }"
      :mask-closable="true"
    >
      <template v-if="detailModel">
        <n-descriptions
          :column="1"
          size="small"
          bordered
          label-placement="left"
        >
          <n-descriptions-item :label="t('companion.detail.modelName')">
            {{ detailModel.value }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('companion.detail.usageCount')">
            <n-tag type="info" size="small" round>{{ detailModel.keys.length }}</n-tag>
          </n-descriptions-item>
        </n-descriptions>

        <n-divider />

        <div class="raw-title">{{ t("companion.detail.usageKeys") }}</div>
        <n-data-table
          :columns="detailKeyColumns"
          :data="detailModel.keys"
          :pagination="false"
          size="small"
          :bordered="true"
          :row-key="(row) => row.key"
        />

        <n-divider />

        <div class="raw-title">{{ t("companion.detail.allModels") }}</div>
        <n-code
          :code="formatAllModels()"
          language="json"
          :show-line-numbers="false"
        />
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NButton,
  NCard,
  NCode,
  NCollapse,
  NCollapseItem,
  NDataTable,
  NDescriptions,
  NDescriptionsItem,
  NDivider,
  NEmpty,
  NModal,
  NSelect,
  NSpace,
  NTabPane,
  NTabs,
  NTag,
  NText,
  NTooltip,
  useMessage,
  type DataTableColumn,
} from "naive-ui";

import {
  apiGet,
  apiPost,
  type CompanionProviderItem,
  type CompanionProvidersResponse,
  type CompanionReplaceResponse,
} from "../api";

const { t } = useI18n();
const message = useMessage();

const loaded = ref<boolean | null>(null);
const items = ref<CompanionProviderItem[]>([]);
const providers = ref<{ id: string; model: string; vendor: string }[]>([]);
// Tab1 精简配置：key = 纯 model 名
const simpleReplacements = reactive<Record<string, string>>({});
// Tab2 主次区分配置：key = "kind:model名"
const advReplacements = reactive<Record<string, string>>({});
const saving = ref(false);
const refreshing = ref(false);
const configMode = ref("");
const configuredCount = ref(0);
const totalKeys = ref(0);
// 陪伴插件运行时实例属性是否与配置一致（false 表示它内存里还是旧值）
const runtimeInSync = ref<boolean | null>(null);

const detailVisible = ref(false);
const detailModel = ref<{
  value: string;
  keys: { key: string; label: string }[];
} | null>(null);

function openDetail(model: { value: string; keys: { key: string; label: string }[] }) {
  detailModel.value = model;
  detailVisible.value = true;
}

function formatAllModels(): string {
  try {
    return JSON.stringify(advRows.value, null, 2);
  } catch {
    return String(advRows.value);
  }
}

// ---------- Tab1 精简配置：按纯 model 名聚合，不区分主/备 ----------
type SimpleRow = {
  value: string;
  keys: { key: string; label: string }[];
  labels: string[];
};
const simpleRows = computed<SimpleRow[]>(() => {
  const map = new Map<string, { key: string; label: string }[]>();
  for (const it of items.value) {
    if (!it.value) continue;
    if (!map.has(it.value)) map.set(it.value, []);
    map.get(it.value)!.push({ key: it.key, label: it.label || it.key });
  }
  return [...map.entries()].map(([value, keys]) => ({
    value,
    keys,
    labels: keys.map((k) => k.key),
  }));
});

// ---------- Tab2 主次区分配置：按 kind + model 聚合 ----------
type AdvRow = {
  aggKey: string;
  value: string;
  kind: string;
  keys: { key: string; label: string }[];
  labels: string[];
};
const advRows = computed<AdvRow[]>(() => {
  const map = new Map<string, { key: string; label: string }[]>();
  const kindOf = new Map<string, string>();
  for (const it of items.value) {
    if (!it.value) continue;
    const aggKey = `${it.kind || "main"}:${it.value}`;
    if (!map.has(aggKey)) {
      map.set(aggKey, []);
      kindOf.set(aggKey, it.kind || "main");
    }
    map.get(aggKey)!.push({ key: it.key, label: it.label || it.key });
  }
  return [...map.entries()].map(([aggKey, keys]) => {
    const value = aggKey.slice(aggKey.indexOf(":") + 1);
    const kind = kindOf.get(aggKey) || "main";
    return { aggKey, value, kind, keys, labels: keys.map((k) => k.key) };
  });
});

const unconfiguredItems = computed(() =>
  items.value
    .filter((it) => !it.configured)
    .map((it) => ({ key: it.key, label: it.label || it.key })),
);

const unconfiguredCount = computed(() => unconfiguredItems.value.length);

const unconfiguredTitle = computed(() =>
  t("companion.sectionUnconfigured", { n: unconfiguredItems.value.length }),
);

// label 展示用 "vendor · model"（含 xx/xx/xx 路径形式）
function modelLabel(p: { model: string; vendor: string }): string {
  if (p.vendor && p.vendor !== p.model) {
    return `${p.vendor} · ${p.model}`;
  }
  return p.model;
}

// NSelect 的 value 必须是 provider id：陪伴插件 config 里存的是 provider id，
// 写回 provider id 才能被陪伴插件下拉框正确匹配/显示。label 用 vendor · model。
function availableOptions(value: string): { label: string; value: string }[] {
  const opts: { label: string; value: string }[] = providers.value.map((p) => ({
    label: modelLabel(p),
    value: p.id,
  }));
  // 去重（按 provider id）
  const seen = new Set<string>();
  const dedup = opts.filter((o) => {
    if (seen.has(o.value)) return false;
    seen.add(o.value);
    return true;
  });
  // 如果原值不在列表里（如旧版缓存），加在最前面
  if (value && !seen.has(value)) {
    dedup.unshift({ label: value, value });
  }
  return [{ label: t("companion.selectNone"), value: "" }, ...dedup];
}

function persistReplacements() {
  try {
    localStorage.setItem(
      "model_panel.companion.replacements",
      JSON.stringify({ simple: simpleReplacements, advanced: advReplacements }),
    );
  } catch { /* ignore */ }
}

// ---------- 保存 ----------
async function save() {
  const replacementsList: { old: string; replacement: string; kind: string }[] = [];
  // Tab1 精简配置：old = model 名，kind = "any"（主备都替换）
  for (const [model, newValue] of Object.entries(simpleReplacements)) {
    if (!newValue || newValue === model) continue;
    replacementsList.push({ old: model, replacement: newValue, kind: "any" });
  }
  // Tab2 主次区分配置：old = model 名，kind 从 aggKey 拆出
  for (const [aggKey, newValue] of Object.entries(advReplacements)) {
    if (!newValue) continue;
    const idx = aggKey.indexOf(":");
    const kind = idx >= 0 ? aggKey.slice(0, idx) : "main";
    const old = idx >= 0 ? aggKey.slice(idx + 1) : aggKey;
    if (newValue === old) continue;
    replacementsList.push({ old, replacement: newValue, kind });
  }
  if (!replacementsList.length) {
    message.warning(t("companion.saveEmpty"));
    return;
  }
  saving.value = true;
  try {
    const res = await apiPost<CompanionReplaceResponse>(
      "/panel/companion/replace",
      { replacements: replacementsList },
    );
    if (res.ok) {
      message.success(
        t("companion.saveSuccess", {
          n: res.changed_count ?? 0,
          m: res.main_count ?? 0,
          f: res.fallback_count ?? 0,
        }),
      );
      simpleReplacements.length = 0;
      advReplacements.length = 0;
      persistReplacements();
      await reload();
      if (runtimeInSync.value === false) {
        message.warning(t("companion.runtimeOutOfSync"));
      }
    } else {
      message.error(t("companion.saveFail", { msg: res.error || "" }));
    }
  } catch (e: any) {
    message.error(t("companion.saveFail", { msg: e?.message || String(e) }));
  } finally {
    saving.value = false;
  }
}

async function reload() {
  try {
    const data = await apiGet<CompanionProvidersResponse>("/panel/companion/providers");
    loaded.value = data.loaded;
    items.value = data.items || [];
    configMode.value = data.config_mode || "";
    configuredCount.value = data.configured_count || 0;
    totalKeys.value = data.total_keys || 0;
    runtimeInSync.value =
      typeof data.runtime_in_sync === "boolean" ? data.runtime_in_sync : null;
  } catch (e) {
    loaded.value = false;
    console.error("加载陪伴插件配置失败", e);
  }
}

async function manualRefresh() {
  if (refreshing.value) return;
  refreshing.value = true;
  try {
    await Promise.all([reload(), loadAvailableModels()]);
    message.success(t("companion.refreshed"));
  } catch (e) {
    console.error(e);
  } finally {
    refreshing.value = false;
  }
}

// ---------- 表格列 ----------
function renderModelTag(row: { value: string; kind?: string }) {
  return row.kind === "fallback"
    ? h(
        NTag,
        {
          size: "small",
          type: "warning",
          round: true,
          bordered: false,
          style: "margin-left: 8px; vertical-align: middle",
        },
        () => t("companion.kindFallback"),
      )
    : h(
        NTag,
        {
          size: "small",
          type: "default",
          round: true,
          bordered: false,
          style: "margin-left: 8px; vertical-align: middle",
        },
        () => t("companion.kindMain"),
      );
}

function renderModelCell(row: { value: string; kind?: string; keys: { key: string; label: string }[]; labels: string[] }) {
  const PREVIEW = 7;
  const total = row.keys.length;
  const previewLabels = row.keys
    .slice(0, PREVIEW)
    .map((k) => k.label)
    .join("\n");
  const rest = total - PREVIEW;
  const tooltipBody =
    rest > 0
      ? previewLabels + "\n" + t("companion.tooltipMore", { n: rest })
      : previewLabels || t("companion.noLabels");
  return h("div", null, [
    h("div", { style: "font-weight: 600" }, [
      row.value,
      row.kind ? renderModelTag(row) : null,
    ]),
    h(
      NTooltip,
      {
        delay: 200,
        placement: "top-start",
        style: "max-width: 360px; white-space: pre-line; word-break: break-all",
      },
      {
        trigger: () =>
          h(
            NTag,
            {
              size: "small",
              type: "info",
              round: true,
              bordered: false,
              style: "margin-top: 4px; cursor: pointer",
              onClick: () =>
                openDetail({ value: row.value, keys: row.keys }),
            },
            () => `${t("companion.labelsTitle")}: ${row.labels.length}`,
          ),
        default: () => tooltipBody,
      },
    ),
  ]);
}

function renderReplaceCell(
  aggKey: string,
  currentValue: string,
  target: Record<string, string>,
) {
  return h(NSelect, {
    value: target[aggKey] ?? "",
    options: availableOptions(currentValue),
    size: "medium",
    filterable: true,
    clearable: true,
    "menu-size": "large",
    onUpdateValue: (v: string) => {
      target[aggKey] = v;
      persistReplacements();
    },
  });
}

const simpleColumns = computed<DataTableColumn<SimpleRow>[]>(() => [
  {
    title: t("companion.colModel"),
    key: "value",
    minWidth: 260,
    render: (row) => renderModelCell(row),
  },
  {
    title: t("common.save"),
    key: "new",
    minWidth: 280,
    render: (row) =>
      renderReplaceCell(row.value, row.value, simpleReplacements),
  },
]);

const advColumns = computed<DataTableColumn<AdvRow>[]>(() => [
  {
    title: t("companion.colModel"),
    key: "value",
    minWidth: 260,
    render: (row) => renderModelCell(row),
  },
  {
    title: t("common.save"),
    key: "new",
    minWidth: 280,
    render: (row) =>
      renderReplaceCell(row.aggKey, row.value, advReplacements),
  },
]);

const unconfiguredColumns = computed<DataTableColumn<{ key: string; label: string }>[]>(() => [
  {
    title: "Key",
    key: "key",
    minWidth: 280,
    render: (row) => h(NText, { code: true }, () => row.key),
  },
  {
    title: t("companion.colLabel"),
    key: "label",
    render: (row) => h(NText, { depth: 3 }, () => row.label),
  },
]);

const detailKeyColumns = computed<DataTableColumn<{ key: string; label: string }>[]>(() => [
  {
    title: "Key",
    key: "key",
    minWidth: 280,
    render: (row) => h(NText, { code: true }, () => row.key),
  },
  {
    title: t("companion.colLabel"),
    key: "label",
    render: (row) => h(NText, { depth: 3 }, () => row.label || row.key),
  },
]);

async function loadAvailableModels() {
  try {
    const data = await apiGet<{
      items: { id: string; name: string; model: string }[];
      models?: string[];
      provider_models?: { model: string; vendor: string; id?: string }[];
    }>("/panel/providers");
    // 优先用后端返回的 provider_models（{model, vendor, id}）：
    // label 拼 "vendor · model"，value 用 provider id（陪伴插件 config 存的是 id）。
    // 旧版 zip 没 id 字段时，从 items 的 id 补齐，仍以 provider id 作为 value。
    const list: { id: string; model: string; vendor: string }[] = [];
    if (Array.isArray(data.provider_models) && data.provider_models.length) {
      for (const m of data.provider_models) {
        if (m && m.model) list.push({ id: m.id || m.model, model: m.model, vendor: m.vendor || "" });
      }
    } else if (Array.isArray(data.models) && data.models.length) {
      const byModel = new Map<string, { id: string; vendor: string }>();
      for (const p of data.items) {
        const m = (p.model || "").trim();
        if (m && !byModel.has(m)) byModel.set(m, { id: p.id || p.name || m, vendor: p.name || "" });
      }
      for (const m of data.models) {
        if (!m) continue;
        const info = byModel.get(m) || { id: m, vendor: "" };
        list.push({ id: info.id, model: m, vendor: info.vendor });
      }
    } else if (data.items) {
      const seen = new Set<string>();
      for (const p of data.items) {
        const m = (p.model || p.id || "").trim();
        if (m && !seen.has(m)) {
          seen.add(m);
          list.push({ id: p.id || p.name || m, model: m, vendor: p.name || "" });
        }
      }
    }
    providers.value = list;
  } catch (e) {
    console.error("加载可用模型失败", e);
  }
}

onMounted(async () => {
  await reload();
  await loadAvailableModels();
  // 恢复用户上次的替换选择（localStorage，避免误操作刷新丢失）
  try {
    const saved = localStorage.getItem("model_panel.companion.replacements");
    if (saved) {
      const obj = JSON.parse(saved);
      const simple = obj?.simple || {};
      const adv = obj?.advanced || {};
      for (const [k, v] of Object.entries(simple)) {
        if (typeof v === "string") simpleReplacements[k] = v;
      }
      for (const [k, v] of Object.entries(adv)) {
        if (typeof v === "string") advReplacements[k] = v;
      }
    }
  } catch { /* ignore */ }
  // 从其它标签页/后台切回时自动刷新配置（不重置用户已选的替换）
  document.addEventListener("visibilitychange", onVisibility);
});

function onVisibility() {
  if (document.visibilityState === "visible") {
    reload();
  }
}
</script>

<style scoped>
.companion-view {
  display: flex;
  flex-direction: column;
}
.toolbar {
  margin-bottom: 8px;
}
.empty {
  padding: 20px 0;
}
.mt-2 {
  margin-top: 8px;
}
.mt-3 {
  margin-top: 12px;
}
.block {
  display: block;
  margin-bottom: 6px;
}
.raw-title {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 6px;
}
/* 弹窗尺寸控制：
   n-modal 默认 .n-modal-scroll-content 有 min-height: 100%，
   会让 modal 在内容较少时也撑满 viewport（关闭按钮被推到顶部之外）。
   这里覆盖 .n-modal / .n-modal-scroll-content / .n-card 三个层级，
   让 modal 自适应内容高度且上限为 viewport - 64px，
   内容区溢出滚动，关闭按钮始终可见。 */
.companion-view :deep(.n-modal-scroll-content) {
  min-height: auto !important;
}
.companion-view :deep(.n-modal) {
  max-height: calc(100vh - 64px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.companion-view :deep(.n-card) {
  max-height: calc(100vh - 64px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
/* naive-ui .n-card 默认 .n-card__content 只有 flex: 1, min-width: 0,
   缺少 min-height: 0 + overflow-y: auto，导致内容溢出时撑高整个 card。
   这里补齐。 */
.companion-view :deep(.n-card__content) {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
}
</style>

<style>
/* 全局弹窗尺寸控制：n-modal 默认 .n-modal-scroll-content 有 min-height: 100%，
   会让 modal 在内容较少时也撑满 viewport。这里覆盖 .n-modal / .n-card /
   .n-card__content 三个层级，让 modal 自适应内容高度且上限为 viewport-64px，
   内容区溢出滚动，关闭按钮始终可见。 */
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
