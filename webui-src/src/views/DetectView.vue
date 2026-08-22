<template>
  <div>
    <div class="card">
      <div class="card-title">检测操作</div>
      <div class="toolbar">
        <button class="btn btn-primary" :disabled="testing" @click="testAll">
          {{ testing ? "检测中…" : "一键检测" }}
        </button>
        <span class="muted">按延迟排序：</span>
        <select v-model="sortKey" class="select">
          <option value="latency">延迟</option>
          <option value="name">名称</option>
        </select>
        <span class="muted">（一键检测时会跳过已关闭的模型）</span>
      </div>
    </div>

    <div class="card">
      <div class="card-title">模型列表（{{ items.length }}）</div>
      <div v-if="!items.length" class="muted">暂无 LLM 模型。</div>
      <div class="table">
        <div class="row head">
          <div class="col check"></div>
          <div class="col name">模型 / Provider</div>
          <div class="col status">状态</div>
          <div class="col latency">延迟</div>
          <div class="col action">操作</div>
        </div>
        <div v-for="item in sortedItems" :key="item.id" class="row">
          <div class="col check">
            <input type="checkbox" v-model="enabled[item.id]" title="一键检测是否跳过此模型" />
          </div>
          <div class="col name">
            <div class="provider-name">{{ item.name || item.id }}</div>
            <div class="provider-sub">
              {{ item.type }}<template v-if="item.model"> · {{ item.model }}</template>
              <span v-if="item.is_default" class="tag-warn">（默认）</span>
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

const sortedItems = computed(() => {
  const list = [...items.value];
  if (sortKey.value === "name") {
    list.sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id, "zh"));
  } else {
    list.sort((a, b) => {
      const la = results[a.id]?.latency_ms;
      const lb = results[b.id]?.latency_ms;
      if (la == null && lb == null) return 0;
      if (la == null) return 1;
      if (lb == null) return -1;
      return la - lb;
    });
  }
  return list;
});

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
.select {
  height: 32px;
  padding: 0 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-2);
  color: var(--text);
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
