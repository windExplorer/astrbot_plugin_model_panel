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
          :bordered="false"
          :single-line="false"
        />

        <n-collapse v-if="unconfiguredItems.length" class="mt-3">
          <n-collapse-item :title="t('companion.sectionUnconfigured', { n: unconfiguredItems.length })" name="unconfigured">
            <n-data-table
              :columns="unconfiguredColumns"
              :data="unconfiguredItems"
              :pagination="false"
              size="small"
              :bordered="false"
            />
          </n-collapse-item>
        </n-collapse>
      </template>
    </n-card>
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
  NEmpty,
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

const usedModels = computed(() => {
  const map = new Map<string, string[]>();
  for (const it of items.value) {
    if (!it.value) continue;
    if (!map.has(it.value)) map.set(it.value, []);
    map.get(it.value)!.push(it.key);
  }
  return [...map.entries()].map(([value, labels]) => ({ value, labels }));
});

const unconfiguredItems = computed(() =>
  items.value.filter((it) => !it.configured).map((it) => ({ key: it.key })),
);

const unconfiguredCount = computed(() => unconfiguredItems.value.length);

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

const columns = computed<DataTableColumn<{ value: string; labels: string[] }>[]>(() => [
  {
    title: t("common.default"),
    key: "value",
    render(row) {
      return h("div", { class: "old-cell" }, [
        h("div", { class: "old-name", title: row.value }, row.value),
        h(NTooltip, null, {
          trigger: () =>
            h("div", { class: "old-sub" }, [
              h(NTag, { size: "tiny", type: "info", round: true }, () =>
                t("companion.labelsTitle") + ": " + row.labels.length,
              ),
            ]),
          default: () => row.labels.join("、") || t("companion.noLabels"),
        }),
      ]);
    },
  },
  {
    title: "→",
    key: "arrow",
    width: 32,
    render: () => h("span", { class: "arrow" }, "→"),
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

const unconfiguredColumns = computed<DataTableColumn<{ key: string }>[]>(() => [
  {
    title: "Key",
    key: "key",
    render: (row) => h("code", { class: "key-cell" }, row.key),
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
}
.old-name {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 320px;
}
.old-sub {
  color: var(--muted);
  font-size: 11px;
  margin-top: 2px;
}
.arrow {
  color: var(--muted);
}
.key-cell {
  font-family: ui-monospace, "SFMono-Regular", "Menlo", monospace;
  font-size: 12px;
  background: var(--panel-2);
  padding: 2px 6px;
  border-radius: 4px;
}
.mt-3 {
  margin-top: 12px;
}
</style>
