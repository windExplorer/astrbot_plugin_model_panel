<template>
  <div class="detect-view">
    <!-- 顶部操作栏：sticky 不随主区滚动 -->
    <n-card :title="t('detect.title')" size="small" class="toolbar-card">
      <n-space align="center" wrap>
        <n-button type="primary" :loading="testing" @click="testAll">
          {{ testing ? t("detect.btnAllLoading") : t("detect.btnAll") }}
        </n-button>
        <n-button quaternary :loading="refreshing" @click="manualRefresh">
          {{ t("detect.btnRefresh") }}
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

    <!-- 主内容区：每个供应商一张独立表格，标题显示供应商名 + 模型数 -->
    <n-card :title="t('detect.groupTitle', { count: items.length })" size="small" class="content-card">
      <n-empty v-if="!items.length && !loadingFailed" :description="t('detect.groupNone')" />
      <n-empty
        v-else-if="!items.length && loadingFailed"
        :description="t('detect.loadFailed')"
      >
        <template #extra>
          <n-text depth="3" style="font-size: 12px">{{ loadingErrorMsg }}</n-text>
          <div style="margin-top: 12px">
            <n-button size="small" type="primary" @click="manualRefresh">
              {{ t("detect.btnRefresh") }}
            </n-button>
          </div>
        </template>
      </n-empty>

      <div v-else class="groups">
        <section
          v-for="group in groupedRows"
          :key="group.name"
          class="group-block"
        >
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
            :scroll-x="1200"
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
      style="max-width: 720px; width: calc(100vw - 48px)"
      :style="{ maxHeight: 'calc(100vh - 64px)' }"
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

const items = ref<ProviderItem[]>([]);
const results = reactive<Record<string, TestResult>>({});
const pending = reactive<Record<string, boolean>>({});
const enabled = reactive<Record<string, boolean>>({});
const testing = ref(false);
const refreshing = ref(false);
const sortKey = ref<"latency" | "name" | "status">("latency");
const lastSessionStats = ref<{
  ok_count: number;
  fail_count: number;
  skip_count: number;
  total: number;
} | null>(null);
const loadingFailed = ref(false);
const loadingErrorMsg = ref("");

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

// ---------- 排序 ----------
function rowKey(row: ProviderItem) {
  return row.id;
}
const sortOptions = computed(() => [
  { label: t("detect.sort.latency"), value: "latency" },
  { label: t("detect.sort.name"), value: "name" },
  { label: t("detect.sort.status"), value: "status" },
]);

// 按供应商分组：[{name, items[]}, ...]
const groupedRows = computed<{ name: string; items: ProviderItem[] }[]>(() => {
  const groups = new Map<string, ProviderItem[]>();
  for (const item of items.value) {
    const g = item.name || t("detect.groupName");
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g)!.push(item);
  }
  const orderedGroups = [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0], "zh"));
  const result: { name: string; items: ProviderItem[] }[] = [];
  for (const [name, list] of orderedGroups) {
    const sorted = [...list];
    if (sortKey.value === "name") {
      sorted.sort((a, b) => (a.model || a.id).localeCompare(b.model || b.id, "zh"));
    } else if (sortKey.value === "status") {
      sorted.sort((a, b) => {
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
      sorted.sort((a, b) => {
        const la = results[a.id]?.latency_ms;
        const lb = results[b.id]?.latency_ms;
        if (la == null && lb == null) return 0;
        if (la == null) return 1;
        if (lb == null) return -1;
        return la - lb;
      });
    }
    result.push({ name, items: sorted });
  }
  return result;
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
  // 同步到后端 + localStorage
  persistEnabled();
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

// ---------- 持久化（localStorage + 后端）----------
let persistTimer: ReturnType<typeof setTimeout> | null = null;
function persistEnabled(immediate = false) {
  // 防抖：避免连续勾选时频繁请求
  if (persistTimer) clearTimeout(persistTimer);
  const flush = () => {
    try {
      localStorage.setItem(
        "model_panel.detect.enabled",
        JSON.stringify(enabled),
      );
    } catch { /* ignore */ }
    // 后端持久化：传完整当前集合（缺失的 provider 在后端视为默认勾选）
    apiPost("/panel/preferences", { items: { ...enabled } }).catch(() => { /* ignore */ });
  };
  if (immediate) flush();
  else persistTimer = setTimeout(flush, 300);
}

async function loadPreferences() {
  // 1. 先从后端拉
  let serverPrefs: Record<string, boolean> | null = null;
  try {
    const r = await apiGet<{ items: Record<string, boolean> }>(
      "/panel/preferences",
    );
    serverPrefs = r.items || {};
  } catch { /* ignore */ }
  // 2. 合并 localStorage（localStorage 优先级高于后端，仅在初次覆盖）
  try {
    const local = localStorage.getItem("model_panel.detect.enabled");
    if (local) {
      const obj = JSON.parse(local);
      // localStorage 已有：用户之前在此设备有选择 → 用 localStorage
      serverPrefs = { ...(serverPrefs || {}), ...obj };
    }
  } catch { /* ignore */ }
  return serverPrefs || {};
}

async function loadResults() {
  try {
    const r = await apiGet<{ items: Record<string, TestResult> }>(
      "/panel/providers/results",
    );
    for (const k of Object.keys(r.items || {})) {
      results[k] = r.items[k];
    }
  } catch (e) {
    console.warn("拉取最新结果失败", e);
  }
}

async function loadProviders() {
  loadingFailed.value = false;
  loadingErrorMsg.value = "";
  try {
    const data = await apiGet<{ items: ProviderItem[]; models?: string[] }>("/panel/providers");
    items.value = data.items || [];
    if (!items.value.length) {
      loadingFailed.value = true;
      loadingErrorMsg.value = "后端 /panel/providers 返回 items 为空，请检查 AstrBot 是否成功获取到 chat provider";
    }
  } catch (e: any) {
    console.error("loadProviders 失败", e);
    items.value = [];
    loadingFailed.value = true;
    loadingErrorMsg.value = String(e?.message || e || "未知错误");
    return;
  }
  // 默认勾选
  for (const it of items.value) {
    if (!(it.id in enabled)) enabled[it.id] = true;
  }
}

// ---------- 检测 ----------
async function testOne(id: string) {
  pending[id] = true;
  try {
    const r = await apiPost<TestResult>("/panel/providers/test", { id });
    results[id] = r;
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
          },
          onDone: (e) => {
            lastSessionStats.value = {
              ok_count: e.ok_count,
              fail_count: e.fail_count,
              skip_count: e.skip_count,
              total: e.total,
            };
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

async function manualRefresh() {
  if (refreshing.value) return;
  refreshing.value = true;
  try {
    await Promise.all([loadProviders(), loadResults()]);
    message.success(t("detect.refreshed"));
  } catch (e) {
    console.error(e);
  } finally {
    refreshing.value = false;
  }
}

// ---------- 表格列（固定宽度，所有列对齐）----------
const COL = {
  check: 56,
  provider: 220,
  vendor: 160,
  status: 110,
  latency: 100,
  result: 220,
  retry: 70,
  action: 110,
};
const columns = computed<DataTableColumn<ProviderItem>[]>(() => [
  {
    title: t("detect.colCheck"),
    key: "check",
    width: COL.check,
    align: "center",
    render(row) {
      return h(NCheckbox, {
        checked: enabled[row.id] !== false,
        onUpdateChecked: (v: boolean) => {
          enabled[row.id] = v;
          persistEnabled();
        },
      });
    },
  },
  {
    title: t("detect.colModel"),
    key: "model",
    width: COL.provider,
    render(row) {
      return h("div", null, [
        h("div", { style: "font-weight: 600" }, [
          row.name || row.id,
          row.is_default
            ? h(NTag, { size: "tiny", type: "warning", style: "margin-left: 6px", round: true },
                () => t("common.default"))
            : null,
        ]),
        h("div", { style: "color: var(--muted); font-size: 12px; word-break: break-all" }, [
          // 完整 model 字符串，包括 xx/xx/xx 路径形式，绝不截断
          row.model || row.type || row.id || "",
        ]),
      ]);
    },
  },
  {
    title: t("detect.colStatus"),
    key: "status",
    width: COL.status,
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
    width: COL.latency,
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
    width: COL.result,
    render(row) {
      const r = results[row.id];
      if (!r) return h(NText, { depth: 3 }, () => "—");
      const tag = resultTagFor(r);
      return h("div", { style: "display: flex; flex-direction: column; gap: 2px; align-items: flex-start; width: 100%" }, [
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
          ? h(NText, {
              depth: 3,
              style:
                "font-size: 12px; width: 100%; word-break: break-all; " +
                "white-space: normal; overflow: hidden; text-overflow: ellipsis; " +
                "display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;",
            }, () => r.error)
          : null,
      ]);
    },
  },
  {
    title: t("detect.colRetry"),
    key: "retry",
    width: COL.retry,
    align: "center",
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
    width: COL.action,
    fixed: "right",
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
  // 并行加载 providers + preferences + results
  try {
    const [_, prefs] = await Promise.all([
      loadProviders(),
      loadPreferences().then((p) => {
        for (const [k, v] of Object.entries(p)) {
          enabled[k] = v;
        }
        return p;
      }),
    ]);
  } catch (e) {
    console.error("初始化失败", e);
  }
  await loadResults();
});
</script>

<style scoped>
.detect-view {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.toolbar-card {
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
  gap: 18px;
}
.group-block {
  display: flex;
  flex-direction: column;
}
.group-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 2px 8px;
}
.group-name {
  font-weight: 700;
  font-size: 14px;
  color: var(--accent-2);
}
</style>

<style>
/* 全局覆盖：弹窗尺寸控制（不分 view）。
   n-modal 默认 .n-modal-scroll-content 有 min-height: 100%，
   会让 modal 在内容较少时也撑满 viewport（关闭按钮被推到顶部之外）。
   这里覆盖 .n-modal / .n-card / .n-card__content 三个层级，
   让 modal 自适应内容高度且上限为 viewport - 64px，
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
