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
          :bordered="true"
          :single-line="false"
          :row-key="(row) => row.value"
          :scroll-x="900"
        />

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

const detailVisible = ref(false);
const detailModel = ref<{
  value: string;
  labels: string[];
  keys: { key: string; label: string }[];
} | null>(null);

function openDetail(model: {
  value: string;
  labels: string[];
  keys: { key: string; label: string }[];
}) {
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
  const opts: { label: string; value: string }[] = providers.value.map((p) => ({
    label: p,
    value: p,
  }));
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

const columns = computed<
  DataTableColumn<{
    value: string;
    labels: string[];
    keys: { key: string; label: string }[];
  }>[]
>(() => [
  {
    title: t("companion.colModel"),
    key: "value",
    minWidth: 260,
    render(row) {
      // 悬浮 tooltip：最多展示前 TOOLTIP_PREVIEW 个 key 的中文标签，
      // 其余折叠为"+N 个 · 点击查看"，避免超长溢出屏幕被截断。
      const PREVIEW = 7;
      const total = row.keys.length;
      const previewLabels = row.keys
        .slice(0, PREVIEW)
        .map((k) => k.label)
        .join("\n");
      const rest = total - PREVIEW;
      const tooltipBody =
        rest > 0
          ? previewLabels +
            "\n" +
            t("companion.tooltipMore", { n: rest })
          : previewLabels || t("companion.noLabels");
      return h("div", null, [
        // 模型名：普通 NText，不再是按钮
        h("div", { style: "font-weight: 600" }, row.value),
        // 适用于：NTag 包裹 + NTooltip 预览 + 点击弹窗
        h(
          NTooltip,
          {
            delay: 200,
            placement: "top-start",
            // 控制 tooltip 内容换行与最大宽度，避免屏幕边缘被截断
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
                  onClick: () => openDetail(row),
                },
                () => `${t("companion.labelsTitle")}: ${row.labels.length}`,
              ),
            default: () => tooltipBody,
          },
        ),
      ]);
    },
  },
  {
    title: t("common.save"),
    key: "new",
    minWidth: 240,
    render(row) {
      return h(NSelect, {
        value: replacements[row.value] ?? "",
        options: availableOptions(row.value),
        size: "small",
        onUpdateValue: (v: string) => {
          replacements[row.value] = v;
          // 持久化用户的替换选择（localStorage，避免误刷新丢失）
          try {
            localStorage.setItem(
              "model_panel.companion.replacements",
              JSON.stringify(replacements),
            );
          } catch { /* ignore */ }
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

onMounted(async () => {
  await reload();
  try {
    const data = await apiGet<{
      items: { id: string; name: string; model: string }[];
      models: string[];
    }>("/panel/providers");
    // 优先用后端返回的去重 models 列表，避免 NSelect 里出现多个相同名字的选项
    const set = new Set<string>();
    if (Array.isArray(data.models) && data.models.length) {
      data.models.forEach((m) => m && set.add(m));
    } else if (data.items) {
      data.items.forEach((p) => (p.model || p.id) && set.add(p.model || p.id));
    }
    providers.value = [...set];
  } catch (e) {
    console.error("加载可用模型失败", e);
  }
  // 恢复用户上次的替换选择（localStorage，避免误操作刷新丢失）
  try {
    const saved = localStorage.getItem("model_panel.companion.replacements");
    if (saved) {
      const obj = JSON.parse(saved);
      for (const [k, v] of Object.entries(obj || {})) {
        if (typeof v === "string") replacements[k] = v;
      }
    }
  } catch { /* ignore */ }
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
