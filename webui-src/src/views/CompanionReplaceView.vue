<template>
  <div class="companion-view">
    <n-card :title="t('companion.title')" size="small">
      <n-text v-if="loaded === false" depth="3">{{ t("companion.notLoaded") }}</n-text>
      <template v-else>
        <n-space align="center" wrap class="toolbar">
          <n-button type="primary" :loading="saving" @click="save">
            {{ saving ? t("companion.btnSaving") : t("companion.btnSave") }}
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

        <!-- 主列表：已配置项 + 替换下拉 -->
        <n-empty
          v-if="!usedModels.length"
          :description="t('companion.usedModelsEmpty')"
          class="empty"
        />

        <n-data-table
          v-else
          :columns="columns"
          :data="usedModels"
          :pagination="false"
          size="small"
          :bordered="true"
          :single-line="false"
          :row-key="(row) => row.value"
        />

        <!-- 未配置项（默认折叠 + 说明） -->
        <n-collapse class="mt-3" :default-expanded-names="[]">
          <n-collapse-item :title="unconfiguredTitle" name="unconfigured">
            <template #header-extra>
              <n-tag size="small" type="warning">{{ unconfiguredItems.length }}</n-tag>
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

    <!-- 模型使用详情弹窗：点模型名打开 -->
    <n-modal
      v-model:show="detailVisible"
      preset="card"
      :title="t('companion.detail.title')"
      style="max-width: 720px"
    >
      <template v-if="detailModel">
        <n-descriptions
          :column="1"
          size="small"
          bordered
          label-placement="left"
          class="detail-desc"
        >
          <n-descriptions-item :label="t('companion.detail.modelName')">
            {{ detailModel.value }}
          </n-descriptions-item>
          <n-descriptions-item :label="t('companion.detail.usageCount')">
            <n-tag type="info" size="small">{{ detailModel.labels.length }}</n-tag>
          </n-descriptions-item>
        </n-descriptions>

        <n-divider />

        <div class="raw-title">{{ t("companion.detail.usageKeys") }}</div>
        <n-data-table
          :columns="detailKeyColumns"
          :data="detailModel.keys || []"
          :pagination="false"
          size="small"
          :bordered="true"
          :row-key="(row) => row.key"
        />

        <n-divider />

        <div class="raw-title">{{ t("companion.detail.allModels") }}</div>
        <pre class="raw-json">{{ formatAllModels() }}</pre>
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
} from "../api";

const { t } = useI18n();
const message = useMessage();

const loaded = ref<boolean | null>(null);
const items = ref<CompanionProviderItem[]>([]);
const providers = ref<string[]>([]);
const replacements = reactive<Record<string, string>>({});
const saving = ref(false);
const configMode = ref("");
const configuredCount = ref(0);
const totalKeys = ref(0);

// 详情弹窗
const detailVisible = ref(false);
const detailModel = ref<{
  value: string;
  labels: string[];
  keys: { key: string; label: string }[];
} | null>(null);

function openDetail(model: { value: string; labels: string[]; keys: { key: string; label: string }[] }) {
  detailModel.value = model;
  detailVisible.value = true;
}

function formatAllModels(): string {
  try {
    return JSON.stringify(usedModels.value, null, 2);
  } catch {
    return String(usedModels.value);
  }
}

// value(模型名) -> [{ key, label }, ...]
const usedModels = computed(() => {
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

const unconfiguredItems = computed(() =>
  items.value
    .filter((it) => !it.configured)
    .map((it) => ({ key: it.key, label: it.label || it.key })),
);

const unconfiguredCount = computed(() => unconfiguredItems.value.length);

const unconfiguredTitle = computed(() =>
  t("companion.sectionUnconfigured", { n: unconfiguredItems.value.length }),
);

function availableOptions(value: string): { label: string; value: string }[] {
  const opts: { label: string; value: string }[] = providers.value.map((p) => ({ label: p, value: p }));
  if (value && !providers.value.includes(value)) {
    opts.unshift({ label: value, value });
  }
  return [{ label: t("companion.selectNone"), value: "" }, ...opts];
}

async function save() {
  const replacementsList: { old: string; replacement: string }[] = [];
  for (const [oldValue, newValue] of Object.entries(replacements)) {
    if (newValue && newValue !== oldValue) {
      replacementsList.push({ old: oldValue, replacement: newValue });
    }
  }
  if (!replacementsList.length) {
    message.warning(t("companion.saveEmpty"));
    return;
  }
  saving.value = true;
  try {
    const res = await apiPost<{ ok: boolean; changed_count: number; error?: string }>(
      "/panel/companion/replace",
      { replacements: replacementsList },
    );
    if (res.ok) {
      message.success(t("companion.saveSuccess", { n: res.changed_count }));
      await reload();
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
  } catch (e) {
    loaded.value = false;
    console.error("加载陪伴插件配置失败", e);
  }
}

const columns = computed<DataTableColumn<{ value: string; labels: string[]; keys: { key: string; label: string }[] }>[]>(() => [
  {
    title: t("companion.colModel"),
    key: "value",
    minWidth: 240,
    render(row) {
      return h("div", { class: "old-cell" }, [
        h(
          "button",
          {
            class: "model-link",
            title: t("companion.detail.clickToView"),
            onClick: () => openDetail(row),
          },
          row.value,
        ),
        h("div", { class: "old-sub" }, [
          h(NTooltip, null, {
            trigger: () =>
              h(NTag, { size: "tiny", type: "info", round: true }, () =>
                t("companion.labelsTitle") + ": " + row.labels.length,
              ),
            default: () => row.labels.join("、") || t("companion.noLabels"),
          }),
        ]),
      ]);
    },
  },
  {
    title: t("common.save"),
    key: "new",
    render(row) {
      return h(NSelect, {
        value: replacements[row.value] ?? "",
        options: availableOptions(row.value),
        size: "small",
        onUpdateValue: (v: string) => {
          replacements[row.value] = v;
        },
      });
    },
  },
]);

const unconfiguredColumns = computed<DataTableColumn<{ key: string; label: string }>[]>(() => [
  {
    title: "Key",
    key: "key",
    minWidth: 280,
    render: (row) =>
      h("code", { class: "key-cell", title: row.key }, row.key),
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
    render: (row) => h("code", { class: "key-cell" }, row.key),
  },
  {
    title: t("companion.colLabel"),
    key: "label",
    render: (row) => h(NText, { depth: 3 }, () => row.label || row.key),
  },
]);

onMounted(async () => {
  await reload();
  try {
    const data = await apiGet<{ items: { id: string; name: string; model: string }[] }>("/panel/providers");
    providers.value = (data.items || []).map((p) => p.model || p.id).filter(Boolean);
  } catch (e) {
    console.error("加载可用模型失败", e);
  }
});
</script>

<style scoped>
.companion-view {
  display: flex;
  flex-direction: column;
}
.toolbar {
  margin-bottom: 12px;
}
.empty {
  padding: 20px 0;
}
.old-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.model-link {
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  color: var(--accent-2);
  font-weight: 600;
  font-size: 14px;
  text-align: left;
  text-decoration: underline dotted;
}
.model-link:hover {
  color: var(--accent);
}
.old-sub {
  font-size: 11px;
}
.key-cell {
  font-family: ui-monospace, "SFMono-Regular", "Menlo", monospace;
  font-size: 12px;
  background: var(--panel-2);
  padding: 2px 6px;
  border-radius: 4px;
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
.detail-desc {
  margin-bottom: 8px;
}
.raw-title {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 6px;
}
.raw-json {
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px 12px;
  font-family: ui-monospace, "SFMono-Regular", "Menlo", monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 280px;
  overflow: auto;
  margin: 0;
}
</style>
