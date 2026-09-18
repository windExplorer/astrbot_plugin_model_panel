<template>
  <div class="plugins-view">
    <n-alert type="info" :show-icon="true" class="mb">
      {{ t("plugins.explain") }}
    </n-alert>

    <n-card size="small">
      <n-space align="center" wrap>
        <n-button type="primary" size="small" :loading="loading" @click="reload(true)">
          {{ t("plugins.btnRefresh") }}
        </n-button>
        <n-input
          v-model:value="keyword"
          size="small"
          clearable
          :placeholder="t('plugins.searchPlaceholder')"
          style="width: 220px"
        />
        <n-checkbox v-model:checked="showLow" size="small">
          {{ t("plugins.showLow") }}
        </n-checkbox>
        <n-checkbox v-model:checked="hideEmpty" size="small">
          {{ t("plugins.hideEmpty") }}
        </n-checkbox>
        <n-text depth="3" style="font-size: 12px">
          {{
            t("plugins.stats", {
              p: stats.plugins_configured,
              total: stats.plugins_total,
              n: stats.entries_total,
              m: stats.providers_total,
            })
          }}
        </n-text>
      </n-space>

      <n-divider style="margin: 12px 0" />

      <n-space align="center" wrap>
        <n-text strong style="font-size: 13px">{{ t("plugins.batchTitle") }}</n-text>
        <n-select
          v-model:value="batchOld"
          size="small"
          filterable
          clearable
          :options="usedValueOptions"
          :placeholder="t('plugins.batchOld')"
          style="width: 260px"
        />
        <span class="arrow">→</span>
        <n-select
          v-model:value="batchNew"
          size="small"
          filterable
          tag
          clearable
          :options="providerOptions"
          :placeholder="t('plugins.batchNew')"
          style="width: 300px"
        />
        <n-button
          type="warning"
          size="small"
          :disabled="!canBatch"
          :loading="batchSaving"
          @click="applyBatch"
        >
          {{ t("plugins.btnBatchApply") }}
        </n-button>
        <n-text depth="3" style="font-size: 12px">{{ t("plugins.batchHint") }}</n-text>
      </n-space>
    </n-card>

    <n-text v-if="!loading && !visiblePlugins.length" depth="3" class="empty-line">
      {{ t("plugins.emptyAll") }}
    </n-text>

    <n-collapse v-model:expanded-names="expanded" class="mt">
      <n-collapse-item
        v-for="group in visiblePlugins"
        :key="group.name"
        :name="group.name"
      >
        <template #header>
          <n-space align="center" size="small">
            <span class="plugin-name">{{ group.display_name || group.name }}</span>
            <n-tag size="small" round :bordered="false">{{ group.name }}</n-tag>
            <n-text v-if="group.version" depth="3" style="font-size: 12px">
              v{{ group.version }}
            </n-text>
            <n-tag
              size="small"
              round
              :bordered="false"
              :type="group.entries.length ? 'success' : 'default'"
            >
              {{ t("plugins.entriesCount", { n: group.entries.length }) }}
            </n-tag>
            <n-tag v-if="!group.activated" size="small" round type="warning" :bordered="false">
              {{ t("plugins.disabled") }}
            </n-tag>
          </n-space>
        </template>

        <div class="plugin-body">
          <n-text depth="3" class="config-line">
            {{ t("plugins.configFile") }}
            <n-text code style="font-size: 12px">
              {{ group.config_path || t("plugins.configPathUnknown") }}
            </n-text>
          </n-text>

          <n-text v-if="!group.has_config" depth="3" class="block">
            {{ t("plugins.noConfig") }}
          </n-text>
          <n-text v-else-if="!group.entries.length" depth="3" class="block">
            {{ t("plugins.noEntry") }}
          </n-text>
          <n-data-table
            v-else
            :columns="columns"
            :data="group.rows"
            :pagination="false"
            size="small"
            :bordered="true"
            :single-line="false"
            :row-key="(row) => row.key"
            :scroll-x="980"
          />
        </div>
      </n-collapse-item>
    </n-collapse>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NCollapse,
  NCollapseItem,
  NDataTable,
  NDivider,
  NInput,
  NSelect,
  NSpace,
  NTag,
  NText,
  NTooltip,
  useDialog,
  useMessage,
  type DataTableColumn,
} from "naive-ui";

import {
  apiGetPluginModels,
  apiSetPluginModels,
  type PluginModelEntry,
  type PluginModelPlugin,
  type PluginModelProvider,
  type PluginModelSetItem,
} from "../api";

const { t } = useI18n();
const message = useMessage();
const dialog = useDialog();

type Row = { key: string; plugin: PluginModelPlugin; entry: PluginModelEntry };
type Group = PluginModelPlugin & { rows: Row[] };

const loading = ref(false);
const batchSaving = ref(false);
const savingKey = ref("");
const keyword = ref("");
// 低可信项默认隐藏：这类条目大多是插件自己的业务模型字段
// （如 comfyui-anima 的 LoRA 文件名、工作流底模），不是 AstrBot provider
const showLow = ref(false);
const hideEmpty = ref(true);
const expanded = ref<string[]>([]);
const plugins = ref<PluginModelPlugin[]>([]);
const providers = ref<PluginModelProvider[]>([]);
const stats = reactive({
  plugins_total: 0,
  plugins_configured: 0,
  entries_total: 0,
  providers_total: 0,
});

const batchOld = ref("");
const batchNew = ref("");

const KIND_ORDER = ["chat", "tts", "stt", "embedding", "rerank"];

function kindLabel(kind: string): string {
  return t(`plugins.kind.${kind || "other"}`);
}

function rowKey(plugin: string, entry: PluginModelEntry): string {
  return plugin + "|" + entry.path_display;
}

/** 供应商/模型 label 相同时补 provider id 后缀，保证每个渠道可区分；默认项加标记 */
function providerSelectOptions(): any[] {
  const groups = new Map<
    string,
    { label: string; value: string; isDefault: boolean }[]
  >();
  for (const p of providers.value) {
    const kind = p.kind || "chat";
    if (!groups.has(kind)) groups.set(kind, []);
    groups.get(kind)!.push({
      label: p.label || p.id,
      value: p.id,
      isDefault: Boolean(p.is_default),
    });
  }
  const options: any[] = [];
  for (const kind of KIND_ORDER) {
    const items = groups.get(kind);
    if (!items || !items.length) continue;
    const counts = new Map<string, number>();
    for (const it of items) counts.set(it.label, (counts.get(it.label) || 0) + 1);
    options.push({
      type: "group",
      key: kind,
      label: kindLabel(kind),
      children: items.map((it) => {
        let label = it.label;
        if ((counts.get(it.label) || 0) > 1) label = `${label} · ${it.value}`;
        if (it.isDefault) label = `${label} ${t("plugins.defaultMark")}`;
        return { label, value: it.value };
      }),
    });
  }
  return options;
}

const providerOptions = computed<any[]>(() => providerSelectOptions());

/** 某个条目可选的"更换为"选项：当前值不在 provider 目录里时补一条。 */
function optionsFor(entry: PluginModelEntry): any[] {
  const options = providerSelectOptions();
  const known = new Set(providers.value.map((p) => p.id));
  if (entry.value && !known.has(entry.value)) {
    options.unshift({
      label: `${entry.value} ${t("plugins.notLoaded")}`,
      value: entry.value,
    });
  }
  return options;
}

const visiblePlugins = computed<Group[]>(() => {
  const kw = keyword.value.trim().toLowerCase();
  const out: Group[] = [];
  for (const p of plugins.value) {
    if (hideEmpty.value && !p.entries.length) continue;
    let entries = p.entries.filter((e) => showLow.value || e.confidence !== "low");
    if (kw) {
      entries = entries.filter((e) =>
        [
          p.name,
          p.display_name,
          e.path_display,
          e.label,
          e.value,
          e.display,
          e.model,
          e.vendor,
        ]
          .join(" ")
          .toLowerCase()
          .includes(kw),
      );
      if (!entries.length) continue;
    }
    out.push({
      ...p,
      rows: entries.map((e) => ({ key: rowKey(p.name, e), plugin: p, entry: e })),
    });
  }
  return out;
});

/** 批量替换：当前可见条目里出现过的值（含出现次数） */
const usedValueOptions = computed(() => {
  const map = new Map<string, number>();
  for (const group of visiblePlugins.value) {
    for (const { entry } of group.rows) {
      if (!entry.value) continue;
      map.set(entry.value, (map.get(entry.value) || 0) + 1);
    }
  }
  return [...map.entries()]
    .map(([value, count]) => {
      const hit = providers.value.find((p) => p.id === value);
      const label = hit ? hit.label : value;
      return { label: `${label} ×${count}`, value };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
});

const batchTargets = computed<{ group: Group; entry: PluginModelEntry }[]>(() => {
  if (!batchOld.value) return [];
  const out: { group: Group; entry: PluginModelEntry }[] = [];
  for (const group of visiblePlugins.value) {
    for (const { entry } of group.rows) {
      if (entry.value === batchOld.value) out.push({ group, entry });
    }
  }
  return out;
});

const canBatch = computed(
  () =>
    Boolean(batchOld.value) &&
    Boolean(batchNew.value) &&
    batchOld.value !== batchNew.value &&
    batchTargets.value.length > 0,
);

// ---------- 状态标签 ----------
function statusTags(entry: PluginModelEntry): any[] {
  const tags: any[] = [];
  if (entry.conflict) {
    tags.push(
      h(
        NTooltip,
        { delay: 200 },
        {
          trigger: () =>
            h(
              NTag,
              { size: "small", type: "error", round: true, bordered: false },
              () => t("plugins.tagConflict"),
            ),
          default: () => t("plugins.tagConflictTip"),
        },
      ),
    );
  }
  if (!entry.resolved) {
    tags.push(
      h(
        NTooltip,
        { delay: 200 },
        {
          trigger: () =>
            h(
              NTag,
              { size: "small", type: "warning", round: true, bordered: false },
              () => t("plugins.tagUnresolved"),
            ),
          default: () => t("plugins.tagUnresolvedTip"),
        },
      ),
    );
  }
  if (entry.confidence === "low") {
    tags.push(
      h(
        NTooltip,
        { delay: 200 },
        {
          trigger: () =>
            h(
              NTag,
              { size: "small", type: "default", round: true, bordered: false },
              () => t("plugins.tagLow"),
            ),
          default: () => t("plugins.tagLowTip"),
        },
      ),
    );
  }
  if (entry.mirrored) {
    tags.push(
      h(
        NTooltip,
        { delay: 200 },
        {
          trigger: () =>
            h(
              NTag,
              { size: "small", type: "info", round: true, bordered: false },
              () => t("plugins.tagMirrored"),
            ),
          default: () => t("plugins.tagMirroredTip"),
        },
      ),
    );
  }
  if (entry.provider_kind && entry.provider_kind !== "chat") {
    tags.push(
      h(
        NTag,
        { size: "small", type: "info", round: true, bordered: false },
        () => kindLabel(entry.provider_kind),
      ),
    );
  }
  return tags;
}

const columns = computed<DataTableColumn<Row>[]>(() => [
  {
    title: t("plugins.colUse"),
    key: "use",
    minWidth: 260,
    render: (row) =>
      h("div", null, [
        h("div", { style: "font-weight: 600" }, row.entry.label || row.entry.key),
        h(
          NText,
          { depth: 3, code: true, style: "font-size: 12px; word-break: break-all" },
          () => row.entry.path_display,
        ),
        row.entry.hint
          ? h(
              NTooltip,
              { delay: 200, style: "max-width: 420px" },
              {
                trigger: () =>
                  h(
                    NText,
                    { depth: 3, style: "font-size: 12px; cursor: help" },
                    () => "ⓘ " + t("plugins.tipShow"),
                  ),
                default: () => row.entry.hint,
              },
            )
          : null,
      ]),
  },
  {
    title: t("plugins.colCurrent"),
    key: "current",
    minWidth: 220,
    render: (row) =>
      h("div", null, [
        h(
          "div",
          {
            style: row.entry.resolved
              ? "font-weight: 600"
              : "font-weight: 600; color: var(--warn)",
          },
          row.entry.resolved ? row.entry.display : row.entry.value,
        ),
        row.entry.resolved && row.entry.provider_id !== row.entry.display
          ? h(NText, { depth: 3, style: "font-size: 12px; word-break: break-all" }, () =>
              row.entry.provider_id,
            )
          : null,
      ]),
  },
  {
    title: t("plugins.colReplace"),
    key: "replace",
    minWidth: 300,
    render: (row) =>
      h(NSelect, {
        size: "small",
        value: row.entry.value,
        options: optionsFor(row.entry),
        filterable: true,
        tag: true,
        consistentMenuWidth: false,
        loading: savingKey.value === row.key,
        disabled: savingKey.value !== "" && savingKey.value !== row.key,
        placeholder: t("plugins.selectPlaceholder"),
        "menu-size": "large",
        onUpdateValue: (value: string) => setOne(row.plugin, row.entry, value),
      }),
  },
  {
    title: t("plugins.colStatus"),
    key: "status",
    minWidth: 150,
    render: (row) => {
      const tags = statusTags(row.entry);
      if (!tags.length) {
        return h(
          NTag,
          { size: "small", type: "success", round: true, bordered: false },
          () => t("plugins.tagNormal"),
        );
      }
      return h(NSpace, { size: 4, wrap: true }, () => tags);
    },
  },
]);

// ---------- 数据加载 ----------
async function reload(manual = false) {
  loading.value = true;
  try {
    const data = await apiGetPluginModels();
    if (!data.ok) {
      message.error(t("plugins.loadFail", { msg: data.error || "" }));
      return;
    }
    plugins.value = data.plugins || [];
    providers.value = data.providers || [];
    Object.assign(stats, {
      plugins_total: data.stats?.plugins_total || 0,
      plugins_configured: data.stats?.plugins_configured || 0,
      entries_total: data.stats?.entries_total || 0,
      providers_total: data.stats?.providers_total || 0,
    });
    // 默认展开有模型配置的插件，方便一眼看完
    if (manual || !expanded.value.length) {
      expanded.value = plugins.value
        .filter((p) => p.entries.length)
        .slice(0, 8)
        .map((p) => p.name);
    }
    if (manual) message.success(t("plugins.refreshed"));
  } catch (e: any) {
    message.error(t("plugins.loadFail", { msg: e?.message || String(e) }));
  } finally {
    loading.value = false;
  }
}

// ---------- 单项更换 ----------
async function setOne(plugin: PluginModelPlugin, entry: PluginModelEntry, value: string) {
  const next = String(value || "").trim();
  if (next === entry.value) return;
  if (!next) {
    message.warning(t("plugins.clearNotSupported"));
    return;
  }
  savingKey.value = rowKey(plugin.name, entry);
  try {
    const res = await apiSetPluginModels([
      {
        plugin: plugin.name,
        path: entry.path,
        value: next,
        container: entry.container,
      },
    ]);
    if (res.ok) {
      message.success(t("plugins.setSuccess", { n: res.changed_count ?? 0 }));
      if (res.unknown_values?.length) {
        message.warning(t("plugins.unknownValue", { v: res.unknown_values[0] }));
      }
      await reload();
    } else {
      message.error(t("plugins.setFail", { msg: res.error || "" }));
    }
  } catch (e: any) {
    message.error(t("plugins.setFail", { msg: e?.message || String(e) }));
  } finally {
    savingKey.value = "";
  }
}

// ---------- 批量替换 ----------
function applyBatch() {
  const oldValue = batchOld.value;
  const newValue = batchNew.value;
  const targets = batchTargets.value;
  if (!targets.length) return;
  const oldLabel =
    providers.value.find((p) => p.id === oldValue)?.label || oldValue;
  const newLabel =
    providers.value.find((p) => p.id === newValue)?.label || newValue;
  dialog.warning({
    title: t("plugins.batchConfirmTitle"),
    content: t("plugins.batchConfirm", { n: targets.length, old: oldLabel, new: newLabel }),
    positiveText: t("common.confirm"),
    negativeText: t("common.cancel"),
    onPositiveClick: async () => {
      batchSaving.value = true;
      try {
        const items: PluginModelSetItem[] = targets.map(({ group, entry }) => ({
          plugin: group.name,
          path: entry.path,
          value: newValue,
          container: entry.container,
        }));
        const res = await apiSetPluginModels(items);
        if (res.ok) {
          message.success(t("plugins.batchDone", { n: res.changed_count ?? 0 }));
          if (res.errors?.length) {
            message.warning(t("plugins.batchPartial", { msg: res.errors[0].error }));
          }
          batchOld.value = "";
          batchNew.value = "";
          await reload();
        } else {
          message.error(t("plugins.setFail", { msg: res.error || "" }));
        }
      } catch (e: any) {
        message.error(t("plugins.setFail", { msg: e?.message || String(e) }));
      } finally {
        batchSaving.value = false;
      }
    },
  });
}

onMounted(() => reload());
</script>

<style scoped>
.plugins-view {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.mb {
  margin-bottom: 0;
}
.mt {
  margin-top: 4px;
}
.block {
  display: block;
  margin-bottom: 6px;
}
.empty-line {
  padding: 12px 2px;
}
.arrow {
  color: var(--muted);
  font-weight: 700;
}
.plugin-name {
  font-weight: 700;
  font-size: 14px;
}
.plugin-body {
  padding: 4px 2px 8px;
}
.config-line {
  display: block;
  font-size: 12px;
  margin-bottom: 8px;
  word-break: break-all;
}
</style>
