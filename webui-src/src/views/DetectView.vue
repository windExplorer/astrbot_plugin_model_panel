<template>
  <div>
    <div class="card">
      <div class="card-title">检测操作</div>
      <div class="toolbar">
        <button class="btn btn-primary" :disabled="testing" @click="testAll">
          {{ testing ? "检测中…" : "一键检测" }}
        </button>
        <span class="muted">排序：</span>
        <select v-model="sortKey" class="select">
          <option value="latency">延迟</option>
          <option value="name">名称</option>
        </select>
        <span class="sep"></span>
        <button class="btn btn-sm" @click="setAll(true)">全选</button>
        <button class="btn btn-sm" @click="setAll(false)">全不选</button>
        <span class="muted">（勾选=参与一键检测，取消=跳过该模型）</span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">全部模型（{{ items.length }}）</div>
      <div v-if="!items.length" class="muted">暂无 LLM 模型。</div>
      <div v-for="group in groupedItems" :key="group.name" class="group">
        <div class="group-head">
          <span class="group-name">{{ group.name || "未分组" }}</span>
          <span class="group-count">{{ group.items.length }} 个</span>
        </div>
        <div class="table">
          <div class="row head">
            <div class="col check"></div>
            <div class="col name">模型</div>
            <div class="col status">状态</div>
            <div class="col latency">延迟</div>
            <div class="col action">操作</div>
          </div>
          <div v-for="item in group.items" :key="item.id" class="row">
            <div class="col check">
              <input type="checkbox" v-model="enabled[item.id]" title="是否参与一键检测（取消=跳过）" />
            </div>
            <div class="col name">
              <div class="provider-name">
                {{ item.name || item.id }}
                <span v-if="item.is_default" class="tag-warn">（默认）</span>
              </div>
              <div class="provider-sub">
                {{ item.type }}<template v-if="item.model"> · {{ item.model }}</template>
              </div>
            </div>
            <div class="col status">
              <span v-if="results[item.id]" :class="results[item.id].ok ? 'tag-ok' : 'tag-err'">
                {{ results[item.id].ok ? "存活" : "失败" }}
              </span>
              <span v-else-if="pending[item.id]" class="muted">检测中…</span>
              <span v-else class="muted">未检测</span>
            </div>
            <div class="col latency">
              <template v-if="results[item.id]">
                <span v-if="results[item.id].latency_ms != null" :class="latencyClass(results[item.id].latency_ms)">
                  {{ results[item.id].latency_ms }} ms
                </span>
                <span v-else class="muted">{{ results[item.id].error || "–" }}</span>
              </template>
            </div>
            <div class="col action">
              <button class="btn btn-sm" :disabled="pending[item.id]" @click="testOne(item.id)">
                {{ pending[item.id] ? "…" : "单独检测" }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { apiGet, apiPost, ProviderItem, TestResult } from "../api";

const items = ref<ProviderItem[]>([]);
const results = reactive<Record<string, TestResult>>({});
const pending = reactive<Record<string, boolean>>({});
const enabled = reactive<Record<string, boolean>>({});
const testing = ref(false);
const sortKey = ref<"latency" | "name">("latency");

const groupedItems = computed(() => {
  const byGroup = new Map<string, ProviderItem[]>();
  for (const item of items.value) {
    const g = item.name || item.type || "";
    if (!byGroup.has(g)) byGroup.set(g, []);
    byGroup.get(g)!.push(item);
  }
  const groups = [...byGroup.entries()].map(([name, list]) => {
    const arr = [...list];
    if (sortKey.value === "name") {
      arr.sort((a, b) => (a.model || a.id).localeCompare(b.model || b.id, "zh"));
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
  groups.sort((a, b) => (a.name || "").localeCompare(b.name || "", "zh"));
  return groups;
});

function setAll(value: boolean) {
  for (const it of items.value) enabled[it.id] = value;
}

function latencyClass(ms: number): string {
  if (ms < 1500) return "tag-ok";
  if (ms < 5000) return "tag-warn";
  return "tag-err";
}

async function testOne(id: string) {
  pending[id] = true;
  try {
    const r = await apiPost<TestResult>("/panel/providers/test", { id });
    results[id] = r;
  } catch (e) {
    results[id] = { id, name: "", model: "", ok: false, latency_ms: null, error: String((e as Error).message || e) };
  } finally {
    pending[id] = false;
  }
}

async function testAll() {
  testing.value = true;
  const skip = Object.keys(enabled).filter((k) => !enabled[k]);
  try {
    const r = await apiPost<{ items: TestResult[] }>("/panel/providers/test_all", { skip });
    for (const item of r.items || []) results[item.id] = item;
  } catch (e) {
    console.error("一键检测失败", e);
  } finally {
    testing.value = false;
  }
}

onMounted(async () => {
  try {
    const data = await apiGet<{ items: ProviderItem[] }>("/panel/providers");
    items.value = data.items || [];
    for (const it of items.value) enabled[it.id] = true;
  } catch (e) {
    console.error("加载模型列表失败", e);
  }
});
</script>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.sep {
  width: 1px;
  height: 22px;
  background: var(--border);
}
.select {
  height: 32px;
  padding: 0 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-2);
  color: var(--text);
}
.group {
  margin-bottom: 18px;
}
.group:last-child {
  margin-bottom: 0;
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
.group-count {
  color: var(--muted);
  font-size: 12px;
}
.table {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}
.row {
  display: grid;
  grid-template-columns: 40px 1fr 90px 100px 110px;
  align-items: center;
  border-bottom: 1px solid var(--border);
  padding: 8px 12px;
  gap: 8px;
}
.row:last-child {
  border-bottom: none;
}
.row.head {
  background: var(--panel-2);
  font-size: 12px;
  color: var(--muted);
}
.col.check {
  text-align: center;
}
.provider-name {
  font-weight: 600;
}
.provider-sub {
  color: var(--muted);
  font-size: 12px;
}
</style>
