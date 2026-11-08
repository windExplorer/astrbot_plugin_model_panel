<template>
  <div class="v7-view">
    <n-alert type="info" title="陪伴插件 V7：模型配置结构已变更" :show-icon="true">
      <div>
        检测到陪伴插件 <b>{{ version || "V7" }}</b>。V7 取消了旧版 37 个
        「快速响应 / 复杂推理…」全局模型入口，改成
        <b>插件级全局默认 + 按任务分层路由（安装 / 角色 / 会话窗口）</b>。
      </div>
      <div class="mt-1">
        模型控制台只管理下面的「插件级全局默认」；逐任务路由、生图、TTS、搜索等配置
        请到 <b>陪伴小屋 → 模型与连接</b> 修改。V7 只承诺只读的扩展接口，
        因此本插件不会去读写陪伴插件的内部数据库或管理路由。
      </div>
    </n-alert>

    <n-card size="small" class="mt-2">
      <template #header>
        <n-space align="center" wrap>
          <span>插件级全局默认</span>
          <n-tag size="small" type="success" round :bordered="false">可修改</n-tag>
          <n-text depth="3">已配置 {{ configuredCount }} / {{ slots.length }} 处</n-text>
        </n-space>
      </template>
      <template #header-extra>
        <n-button size="small" quaternary @click="notifyRefresh('refresh')">刷新配置</n-button>
      </template>

      <n-tabs type="line">
        <n-tab-pane name="slots" tab="配置清单">
          <n-text depth="3" class="block">
            这里对应陪伴插件配置里的 model 与 voice_reply 两组。留空表示跟随默认，
            下拉选择会立即写入陪伴插件的插件配置。
          </n-text>
          <n-data-table
            :columns="slotColumns"
            :data="slots"
            :pagination="false"
            size="small"
            :bordered="true"
            :single-line="false"
            :row-key="(r) => r.key"
            :scroll-x="960"
            class="mt-1"
          />
        </n-tab-pane>

        <n-tab-pane name="replace" tab="一键替换">
          <n-text depth="3" class="block">
            按当前模型名聚合：选中「替换为」后，上面清单里所有引用该模型的位置
            （含替换规则里的 provider_id）会一起换成新模型；未选择的保持原样。
          </n-text>
          <n-empty v-if="!replaceRows.length" description="当前没有已配置的模型可替换" class="empty" />
          <n-data-table
            v-else
            :columns="replaceColumns"
            :data="replaceRows"
            :pagination="false"
            size="small"
            :bordered="true"
            :single-line="false"
            :row-key="(r) => r.model"
            :scroll-x="900"
          />
          <n-space align="center" class="mt-2" wrap>
            <n-button type="primary" :loading="saving" @click="save">保存替换</n-button>
            <n-button quaternary :disabled="saving" @click="clearSelections">清空选择</n-button>
          </n-space>
        </n-tab-pane>
      </n-tabs>

      <n-alert
        v-if="reloadRequired"
        type="warning"
        title="配置已写入，需要重载陪伴插件"
        class="mt-2"
      >
        V7 会把插件配置解析成运行时策略缓存，仅保存文件不会立刻刷新。
        请在 AstrBot 插件管理里重载陪伴插件（或在「陪伴小屋 → 模型与连接」保存一次配置）。
      </n-alert>
    </n-card>

    <n-card title="由陪伴小屋管理的位置（模型控制台不读写）" size="small" class="mt-2">
      <n-text depth="3" class="block">
        下面是 V7 里同样会引用模型、但不属于插件级默认的位置。它们按任务 / 角色 /
        窗口分层，或存在陪伴插件自己的存储里，请到陪伴小屋逐项确认。
      </n-text>
      <div v-for="area in managedAreas" :key="area.group" class="v7-area">
        <n-space align="center" wrap>
          <n-tag size="small" type="info" round :bordered="false">{{ area.group }}</n-tag>
          <n-text depth="3">{{ area.where }}</n-text>
        </n-space>
        <div class="v7-area-fields">{{ area.fields }}</div>
        <div class="v7-area-note">{{ area.note }}</div>
      </div>
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { computed, h, reactive, ref } from "vue";
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NEmpty,
  NSelect,
  NSpace,
  NTabPane,
  NTabs,
  NTag,
  NText,
  useMessage,
  type DataTableColumn,
} from "naive-ui";
import {
  apiCompanionSet,
  apiPost,
  type CompanionProvidersResponse,
  type CompanionReplaceResponse,
  type CompanionV7ManagedArea,
  type CompanionV7Slot,
} from "../api";

// 陪伴插件 V7 的页面文案全部写简体中文：本插件页不做 i18n。
// V7 的 A 层（插件级默认）由父组件一次性拉取后以 prop 传入，保存动作只发写请求 + emit 刷新。
const props = defineProps({
  data: {
    type: Object as () => CompanionProvidersResponse | null,
    default: null,
  },
  providers: {
    type: Array as () => { id: string; model: string; vendor: string }[],
    default: () => [],
  },
});
// 命名避开 emit 前缀：前端静态守卫的 i18n 正则会把 emit 带引号参数误判成翻译键
const notifyRefresh = defineEmits(["refresh"]);
const message = useMessage();

const saving = ref(false);
const reloadRequired = ref(false);
const replacements = reactive<Record<string, string>>({});

const payload = computed(() => props.data?.v7 || null);
const version = computed(() => payload.value?.version || "");
const slots = computed<CompanionV7Slot[]>(() => payload.value?.slots || []);
const managedAreas = computed<CompanionV7ManagedArea[]>(() => payload.value?.managed_areas || []);
const configuredCount = computed(() => slots.value.filter((s) => s.configured).length);

type ReplaceRow = { model: string; labels: string[] };
const replaceRows = computed<ReplaceRow[]>(() => {
  const map = new Map<string, string[]>();
  for (const s of slots.value) {
    if (!s.configured || !s.model) continue;
    if (!map.has(s.model)) map.set(s.model, []);
    map.get(s.model)!.push(s.label);
  }
  return [...map.entries()].map(([model, labels]) => ({ model, labels }));
});

function modelLabel(p: { model: string; vendor: string }): string {
  return p.vendor && p.vendor !== p.model ? `${p.vendor} · ${p.model}` : p.model;
}

function availableOptions(current: string): { label: string; value: string }[] {
  const counts = new Map<string, number>();
  for (const p of props.providers) {
    const label = modelLabel(p);
    counts.set(label, (counts.get(label) || 0) + 1);
  }
  const seen = new Set<string>();
  const options = props.providers
    .filter((p) => {
      if (seen.has(p.id)) return false;
      seen.add(p.id);
      return true;
    })
    .map((p) => {
      const label = modelLabel(p);
      return {
        label: (counts.get(label) || 0) > 1 ? `${label} · ${p.id}` : label,
        value: p.id,
      };
    });
  if (current && !seen.has(current)) {
    options.unshift({ label: current, value: current });
  }
  return [{ label: "不替换 / 留空", value: "" }, ...options];
}

async function setOne(row: CompanionV7Slot, value: string) {
  if (!row.editable) {
    message.warning("这一项在替换规则的 JSON 内层，请用「一键替换」修改");
    return;
  }
  try {
    const res = await apiCompanionSet([
      { key: row.key, provider_id: value, kind: row.kind },
    ]);
    if (res.ok) {
      message.success(`已保存 ${row.label}`);
      if (res.reload_required) reloadRequired.value = true;
      notifyRefresh("refresh");
    } else {
      message.error(`保存失败：${res.error || "未知错误"}`);
    }
  } catch (e: any) {
    message.error(`保存失败：${e?.message || String(e)}`);
  }
}

function clearSelections() {
  for (const key of Object.keys(replacements)) delete replacements[key];
}

async function save() {
  const list: { old: string; replacement: string; kind: string }[] = [];
  for (const [model, pid] of Object.entries(replacements)) {
    if (pid) list.push({ old: model, replacement: pid, kind: "any" });
  }
  if (!list.length) {
    message.warning("还没有选择要替换成的模型");
    return;
  }
  saving.value = true;
  try {
    const res = await apiPost<CompanionReplaceResponse>("/panel/companion/replace", {
      replacements: list,
    });
    if (res.ok) {
      message.success(`已替换 ${res.changed_count ?? 0} 处配置`);
      clearSelections();
      if (res.reload_required) reloadRequired.value = true;
      notifyRefresh("refresh");
    } else {
      message.error(`替换失败：${res.error || "未知错误"}`);
    }
  } catch (e: any) {
    message.error(`替换失败：${e?.message || String(e)}`);
  } finally {
    saving.value = false;
  }
}

const slotColumns = computed<DataTableColumn<CompanionV7Slot>[]>(() => [
  {
    title: "用途",
    key: "label",
    minWidth: 200,
    render: (row) =>
      h("div", null, [
        row.label,
        row.kind === "fallback"
          ? h(
              NTag,
              {
                size: "small",
                type: "warning",
                round: true,
                bordered: false,
                style: "margin-left: 6px",
              },
              () => "备用",
            )
          : null,
      ]),
  },
  {
    title: "配置位置",
    key: "key",
    minWidth: 280,
    render: (row) => h(NText, { code: true, depth: 3 }, () => row.key),
  },
  {
    title: "当前模型",
    key: "model",
    minWidth: 240,
    render: (row) =>
      row.configured
        ? h(NText, null, () => row.model)
        : h(NText, { depth: 3 }, () => "未配置（跟随默认）"),
  },
  {
    title: "改为",
    key: "new",
    minWidth: 280,
    render: (row) =>
      h(NSelect, {
        size: "small",
        value: row.provider_id,
        options: availableOptions(row.provider_id),
        clearable: true,
        filterable: true,
        disabled: !row.editable,
        placeholder: row.editable ? "选择模型" : "请用一键替换",
        onUpdateValue: (v: string | null) => setOne(row, v ?? ""),
      }),
  },
]);

const replaceColumns = computed<DataTableColumn<ReplaceRow>[]>(() => [
  {
    title: "当前模型",
    key: "model",
    minWidth: 280,
    render: (row) => h("div", { style: "font-weight: 600" }, row.model),
  },
  {
    title: "用到它的位置",
    key: "labels",
    minWidth: 280,
    render: (row) =>
      h(NText, { depth: 3 }, () =>
        `${row.labels.length} 处：${row.labels.slice(0, 4).join("、")}${row.labels.length > 4 ? "…" : ""}`,
      ),
  },
  {
    title: "替换为",
    key: "new",
    minWidth: 300,
    render: (row) =>
      h(NSelect, {
        value: replacements[row.model] ?? "",
        options: availableOptions(""),
        size: "medium",
        filterable: true,
        clearable: true,
        placeholder: "选择新模型",
        onUpdateValue: (v: string | null) => {
          if (v) replacements[row.model] = v;
          else delete replacements[row.model];
        },
      }),
  },
]);
</script>

<style>
.v7-view .mt-1 { margin-top: 6px; }
.v7-view .mt-2 { margin-top: 12px; }
.v7-view .block { display: block; }
.v7-view .empty { padding: 24px 0; }
.v7-area { padding: 10px 0; border-bottom: 1px solid rgba(128, 128, 128, 0.18); }
.v7-area:last-child { border-bottom: none; }
.v7-area-fields { margin-top: 4px; font-size: 12px; opacity: 0.75; word-break: break-all; }
.v7-area-note { margin-top: 2px; font-size: 12px; opacity: 0.65; }
</style>
