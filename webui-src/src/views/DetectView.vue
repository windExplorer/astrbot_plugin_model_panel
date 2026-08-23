<template>
  <div class="detect-view">
    <!-- 顶部操作栏：sticky 不随主区滚动 -->
    <n-card :title="t('detect.title')" size="small" class="toolbar-card">
      <n-space align="center" wrap>
        <n-button type="primary" :loading="testing" @click="testAll">
          {{ testing ? t("detect.btnAllLoading") : t("detect.btnAll") }}
        </n-button>
        <n-divider vertical />
        <n-text depth="3">{{ t("detect.sortBy") }}：</n-text>
        <n-select
          v-model:value="sortKey"
          :options="sortOptions"
          size="small"
          style="width: 140px"
        />
        <n-divider vertical />
        <n-checkbox
          :checked="allChecked"
          :indeterminate="someChecked && !allChecked"
          @update:checked="(v: boolean) => setAll(v)"
        >
          {{ t("detect.selectAll") }}
        </n-checkbox>
        <n-checkbox
          :checked="!someChecked"
          :disabled="allChecked"
          @update:checked="(v: boolean) => setAll(!v)"
        >
          {{ t("detect.selectNone") }}
        </n-checkbox>
        <n-text depth="3" class="hint">{{ t("detect.selectHint") }}</n-text>
      </n-space>
    </n-card>

    <!-- 主内容区 -->
    <n-card :title="t('detect.groupTitle', { count: items.length })" size="small" class="content-card">
      <n-empty v-if="!items.length" :description="t('detect.groupNone')" />

      <div v-else class="groups">
        <section v-for="group in groupedItems" :key="group.name" class="group-block">
          <header class="group-head">
            <span class="group-name">{{ group.name || t("detect.groupName") }}</span>
            <n-tag size="small" :bordered="false" type="default">
              {{ group.items.length }} {{ t("detect.units") }}
            </n-tag>
          </header>
          <n-data-table
            :columns="columns"
            :data="group.items"
            :row-key="rowKey"
            size="small"
            :pagination="false"
            :bordered="true"
            :single-line="false"
            :scroll-x="1100"
          />
        </section>
      </div>
    </n-card>

    <n-card v-if="lastSessionStats" size="small" class="stats-card">
      <n-space align="center" wrap>
        <n-tag :type="lastSessionStats.ok_count > 0 ? 'success' : 'default'" round>
          ✓ {{ lastSessionStats.ok_count }}
        </n-tag>
        <n-tag :type="lastSessionStats.fail_count > 0 ? 'error' : 'default'" round>
          ✗ {{ lastSessionStats.fail_count }}
        </n-tag>
        <n-tag v-if="lastSessionStats.skip_count" type="warning" round>
          ⤴ {{ lastSessionStats.skip_count }}
        </n-tag>
        <n-text depth="3">
          {{ t("detect.streamDoneHint", {
            ok: lastSessionStats.ok_count,
            fail: lastSessionStats.fail_count,
            skip: lastSessionStats.skip_count,
          }) }}
        </n-text>
      </n-space>
    </n-card>

    <!-- 结果详情弹窗 -->
    <n-modal
      v-model:show="detailVisible"
      preset="card"
      :title="t('detect.detailTitle')"
      style="max-width: 720px"
    >
      <template v-if="detailRow">
        <n-descriptions
          :column="1"
          size="small"
          bordered
          label-placement="left"
        >
          <n-descriptions-item :label="t('detect.colModel')">
            {{ detailRow.name || detailRow.id }}
            <span v-if="detailRow.model" style="color: var(--muted)"> · {{ detailRow.model }}</span>
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
        <div style="font-size: 12px; color: var(--muted); margin-bottom: 6px">
          {{ t("detect.detail.rawJson") }}
        </div>
        <n-code :code="formatRawJson(detailRow)" language="json" :show-line-numbers="false" />
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
  NCheckbox,
  NCode,
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
  startTestAllStream,
  type ProviderItem,
  type TestResult,
} from "../api";

const { t } = useI18n();
const message = useMessage();

const STORAGE_KEY_RESULTS = "model_panel.detect.results";
const STORAGE_KEY_ENABLED = "model_panel.detect.enabled";
const STORAGE_KEY_LAST_DONE = "model_panel.detect.lastDone";

const items = ref<ProviderItem[]>([]);
const results = reactive<Record<string, TestResult>>({});
const pending = reactive<Record<string, boolean>>({});
const enabled = reactive<Record<string, boolean>>({});
const testing = ref(false);
const sortKey = ref<"latency" | "name" | "status">("latency");
const lastSessionStats = ref<{
  ok_count: number;
  fail_count: number;
  skip_count: number;
  total: number;
} | null>(null);

// 详情弹窗
const detailVisible = ref(false);
const detailRow = ref<TestResult | null>(null);

function openDetail(row: ProviderItem) {
  const r = results[row.id];
  if (!r) {
    message.info(t("detect.detail.noResult"));
    return;
  }
  detailRow.value = r;
  detailVisible.value = true;
}

function formatTime(ts?: number): string {
  if (!ts) return "—";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return String(ts);
  }
}

function formatRawJson(row: TestResult): string {
  try {
    return JSON.stringify(row, null, 2);
  } catch {
    return String(row);
  }
}

// ---------- 持久化 ----------
function saveResults() {
  try {
    localStorage.setItem(STORAGE_KEY_RESULTS, JSON.stringify(results));
  } catch { /* ignore */ }
}
function saveEnabled() {
  try {
    localStorage.setItem(STORAGE_KEY_ENABLED, JSON.stringify(enabled));
  } catch { /* ignore */ }
}
function saveLastDone() {
  try {
    if (lastSessionStats.value) {
      localStorage.setItem(STORAGE_KEY_LAST_DONE, JSON.stringify(lastSessionStats.value));
    }
  } catch { /* ignore */ }
}
function loadPersisted() {
  try {
    const r = localStorage.getItem(STORAGE_KEY_RESULTS);
    if (r) {
      const obj = JSON.parse(r);
      for (const k of Object.keys(obj || {})) (results as any)[k] = obj[k];
    }
    const e = localStorage.getItem(STORAGE_KEY_ENABLED);
    if (e) {
      const obj = JSON.parse(e);
      for (const k of Object.keys(obj || {})) (enabled as any)[k] = obj[k];
    }
    const ld = localStorage.getItem(STORAGE_KEY_LAST_DONE);
    if (ld) lastSessionStats.value = JSON.parse(ld);
  } catch { /* ignore */ }
}

// ---------- 排序 / 分组 ----------
function rowKey(row: ProviderItem) {
  return row.id;
}
const sortOptions = computed(() => [
  { label: t("detect.sort.latency"), value: "latency" },
  { label: t("detect.sort.name"), value: "name" },
  { label: t("detect.sort.status"), value: "status" },
]);

const groupedItems = computed(() => {
  const byGroup = new Map<string, ProviderItem[]>();
  for (const item of items.value) {
    const g = item.name || t("detect.groupName");
    if (!byGroup.has(g)) byGroup.set(g, []);
    byGroup.get(g)!.push(item);
  }
  const groups = [...byGroup.entries()].map(([name, list]) => {
    const arr = [...list];
    if (sortKey.value === "name") {
      arr.sort((a, b) => (a.model || a.id).localeCompare(b.model || b.id, "zh"));
    } else if (sortKey.value === "status") {
      arr.sort((a, b) => {
        const ra = results[a.id];
        const rb = results[b.id];
        const score = (x?: TestResult) => {
          if (!x) return 3;
          if (x.skipped) return 4;
          if (x.ok) return 1;
          return 2;
        };
        return score(ra) - score(rb);
      });
    } else {
      arr.sort((a, b) => {
        const la = results[a.id]?.latency_ms;
        const lb = results[b.id]?.latency_ms;
        if (la == null && lb == null) return 0;
        if (la == null) return 1;
        if (lb == null) return -1;
        return la - lb;
      });
    }
    return { name, items: arr };
  });
  groups.sort((a, b) => a.name.localeCompare(b.name, "zh"));
  return groups;
});

// ---------- 工具 ----------
const allChecked = computed(() => {
  if (!items.value.length) return false;
  return items.value.every((it) => enabled[it.id] !== false);
});
const someChecked = computed(() => {
  if (!items.value.length) return false;
  return items.value.some((it) => enabled[it.id] !== false);
});

function setAll(value: boolean) {
  for (const it of items.value) enabled[it.id] = value;
  saveEnabled();
}

function latencyClass(ms: number): "success" | "warning" | "error" {
  if (ms < 1500) return "success";
  if (ms < 5000) return "warning";
  return "error";
}

function resultTagFor(item: TestResult): { type: "success" | "warning" | "error" | "default"; label: string } {
  if (item.skipped) return { type: "default", label: t("detect.result.skipped") };
  if (item.ok) return { type: "success", label: t("detect.result.ok") };
  const code = item.error_code || "unknown";
  return { type: "error", label: t(`detect.result.${code}`) };
}

// ---------- 检测 ----------
async function testOne(id: string) {
  pending[id] = true;
  try {
    const r = await apiPost<TestResult>("/panel/providers/test", { id });
    results[id] = r;
    saveResults();
  } catch (e) {
    results[id] = {
      id,
      name: "",
      model: "",
      ok: false,
      latency_ms: null,
      error_code: "unknown",
      error: String((e as Error).message || e),
      retry_count: 0,
    };
    saveResults();
  } finally {
    pending[id] = false;
  }
}

async function testAll() {
  if (testing.value) return;
  testing.value = true;
  const skip = Object.keys(enabled).filter((k) => !enabled[k]);
  for (const k of Object.keys(pending)) pending[k] = false;
  try {
    await new Promise<void>((resolve, reject) => {
      startTestAllStream(
        { skip },
        {
          onStart: () => {
            for (const k of Object.keys(enabled)) {
              if (enabled[k]) pending[k] = true;
            }
          },
          onItem: (e) => {
            const it = e.item;
            results[it.id] = it;
            pending[it.id] = false;
            saveResults();
          },
          onDone: (e) => {
            lastSessionStats.value = {
              ok_count: e.ok_count,
              fail_count: e.fail_count,
              skip_count: e.skip_count,
              total: e.total,
            };
            saveLastDone();
            for (const k of Object.keys(pending)) pending[k] = false;
            resolve();
          },
          onError: (msg) => {
            for (const k of Object.keys(pending)) pending[k] = false;
            reject(new Error(msg));
          },
        },
      );
    });
  } catch (e) {
    console.error("一键检测失败", e);
  } finally {
    testing.value = false;
    for (const k of Object.keys(pending)) pending[k] = false;
  }
}

// ---------- 表格列 ----------
const columns = computed<DataTableColumn<ProviderItem>[]>(() => [
  {
    title: t("detect.colCheck"),
    key: "check",
    width: 60,
    render(row) {
      return h(NCheckbox, {
        checked: enabled[row.id] !== false,
        onUpdateChecked: (v: boolean) => {
          enabled[row.id] = v;
          saveEnabled();
        },
      });
    },
  },
  {
    title: t("detect.colModel"),
    key: "model",
    minWidth: 220,
    render(row) {
      return h("div", null, [
        h("div", { style: "font-weight: 600" }, [
          row.name || row.id,
          row.is_default
            ? h(NTag, { size: "tiny", type: "warning", style: "margin-left: 6px", round: true },
                () => t("common.default"))
            : null,
        ]),
        h("div", { style: "color: var(--muted); font-size: 12px" }, [
          row.type || "",
          row.model ? ` · ${row.model}` : "",
        ]),
      ]);
    },
  },
  {
    title: t("detect.colStatus"),
    key: "status",
    width: 120,
    render(row) {
      const id = row.id;
      if (results[id]) {
        const tag = resultTagFor(results[id]);
        return h(NTag, { type: tag.type, size: "small", round: true }, () => tag.label);
      }
      if (pending[id]) {
        return h(NText, { depth: 3 }, () => t("detect.statusPending"));
      }
      return h(NText, { depth: 3 }, () => t("detect.statusUnchecked"));
    },
  },
  {
    title: t("detect.colLatency"),
    key: "latency",
    width: 110,
    render(row) {
      const r = results[row.id];
      if (!r) return h(NText, { depth: 3 }, () => "—");
      if (r.latency_ms != null) {
        const cls = latencyClass(r.latency_ms);
        return h(NTag, { type: cls, size: "small", bordered: false }, () =>
          t("detect.latencyMs", { n: r.latency_ms }),
        );
      }
      return h(NText, { depth: 3 }, () => "—");
    },
  },
  {
    title: t("detect.colResult"),
    key: "result",
    minWidth: 220,
    render(row) {
      const r = results[row.id];
      if (!r) return h(NText, { depth: 3 }, () => "—");
      const tag = resultTagFor(r);
      return h("div", { style: "display: flex; flex-direction: column; gap: 2px; align-items: flex-start; max-width: 280px" }, [
        h(NTag, { type: tag.type, size: "small", round: true }, () => tag.label),
        h(NTooltip, { delay: 200 }, {
          trigger: () =>
            h(NButton, {
              text: true,
              type: "primary",
              size: "tiny",
              onClick: () => openDetail(row),
              style: "font-family: ui-monospace, Menlo, monospace; font-size: 11px; padding: 0; height: auto",
            }, () => r.error_code || (r.ok ? "ok" : "—")),
          default: () => r.error || t("detect.detail.openRaw"),
        }),
        r.error
          ? h(NText, { depth: 3, style: "font-size: 12px; max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap" }, () => r.error)
          : null,
      ]);
    },
  },
  {
    title: t("detect.colRetry"),
    key: "retry",
    width: 80,
    render(row) {
      const r = results[row.id];
      if (!r || (r.retry_count ?? 0) <= 0) return h(NText, { depth: 3 }, () => "—");
      return h(NTooltip, null, {
        trigger: () => h(NTag, { size: "small", type: "info", round: true }, () => `×${r.retry_count}`),
        default: () => t("detect.retryTimes", { n: r.retry_count }),
      });
    },
  },
  {
    title: t("detect.colAction"),
    key: "action",
    width: 130,
    render(row) {
      return h(NButton, {
        size: "small",
        type: "primary",
        ghost: true,
        loading: !!pending[row.id],
        disabled: testing.value,
        onClick: () => testOne(row.id),
      }, () => (pending[row.id] ? t("detect.btnRetrying") : t("detect.btnSingle")));
    },
  },
]);

onMounted(async () => {
  loadPersisted();
  try {
    const data = await apiGet<{ items: ProviderItem[] }>("/panel/providers");
    items.value = data.items || [];
    for (const it of items.value) {
      if (!(it.id in enabled)) enabled[it.id] = true;
    }
    saveEnabled();
    try {
      const r = await apiGet<{ items: Record<string, TestResult> }>("/panel/providers/results");
      for (const k of Object.keys(r.items || {})) {
        results[k] = r.items[k];
      }
      saveResults();
    } catch (e) {
      console.warn("拉取最新结果失败", e);
    }
  } catch (e) {
    console.error("加载模型列表失败", e);
  }
});
</script>

<style scoped>
.detect-view {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.toolbar-card {
  /* 顶部工具栏 sticky 在主区视口顶部，不随滚动消失 */
  position: sticky;
  top: 0;
  z-index: 10;
}
.toolbar-card :deep(.n-card__content) {
  padding: 12px 16px;
}
.hint {
  font-size: 12px;
}
.groups {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.group-block {
  display: flex;
  flex-direction: column;
}
.group-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 2px 8px;
}
.group-name {
  font-weight: 700;
  font-size: 14px;
  color: var(--accent-2);
}
</style>
