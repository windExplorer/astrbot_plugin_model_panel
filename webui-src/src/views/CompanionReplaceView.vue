<template>
  <div>
    <div class="card">
      <div class="card-title">陪伴插件精准模型配置</div>
      <div v-if="!loaded" class="muted">
        {{ loaded === null ? "加载中…" : "未找到陪伴插件（astrbot_plugin_private_companion），请确认已安装并启用。" }}
      </div>
      <template v-else>
        <div class="toolbar">
          <button class="btn btn-primary" :disabled="saving" @click="save">
            {{ saving ? "保存中…" : "保存替换" }}
          </button>
          <span class="muted">已使用模型 {{ usedModels.length }} 个（未选择替换的保持原样）</span>
        </div>

        <div v-if="!usedModels.length" class="muted empty">陪伴插件精准配置中没有已配置的模型。</div>

        <div v-else class="list">
          <div v-for="item in usedModels" :key="item.value" class="row">
            <div class="row-old">
              <div class="old-name" :title="item.value">{{ item.value }}</div>
              <div class="old-sub">{{ item.labels.join("、") }}</div>
            </div>
            <div class="row-arrow">→</div>
            <div class="row-new">
              <select v-model="replacements[item.value]" class="select">
                <option value="">（不替换）</option>
                <option v-for="opt in availableOptions(item.value)" :key="opt" :value="opt">{{ opt }}</option>
              </select>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { apiGet, apiPost, CompanionProviderItem } from "../api";

const loaded = ref<boolean | null>(null);
const items = ref<CompanionProviderItem[]>([]);
const providers = ref<string[]>([]);
const replacements = reactive<Record<string, string>>({});
const saving = ref(false);

const usedModels = computed(() => {
  const map = new Map<string, string[]>();
  for (const it of items.value) {
    if (!it.value) continue;
    if (!map.has(it.value)) map.set(it.value, []);
    map.get(it.value)!.push(it.key);
  }
  return [...map.entries()].map(([value, labels]) => ({ value, labels }));
});

function availableOptions(value: string): string[] {
  const opts = [...providers.value];
  if (value && !opts.includes(value)) opts.unshift(value);
  return opts;
}

async function save() {
  const replacementsList: { old: string; replacement: string }[] = [];
  for (const [oldValue, newValue] of Object.entries(replacements)) {
    if (newValue && newValue !== oldValue) {
      replacementsList.push({ old: oldValue, replacement: newValue });
    }
  }
  if (!replacementsList.length) {
    alert("没有需要替换的模型。");
    return;
  }
  saving.value = true;
  try {
    const res = await apiPost<{ ok: boolean; changed_count: number; error?: string }>("/panel/companion/replace", {
      replacements: replacementsList,
    });
    if (res.ok) {
      alert(`替换成功，共 ${res.changed_count} 处。`);
      await reload();
    } else {
      alert(`替换失败：${res.error || "未知错误"}`);
    }
  } catch (e) {
    alert("替换失败：" + String((e as Error).message || e));
  } finally {
    saving.value = false;
  }
}

async function reload() {
  try {
    const data = await apiGet<{ loaded: boolean; items: CompanionProviderItem[] }>("/panel/companion/providers");
    loaded.value = data.loaded;
    items.value = data.items || [];
  } catch (e) {
    loaded.value = false;
    console.error("加载陪伴插件配置失败", e);
  }
}

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
.toolbar {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.list {
  display: grid;
  gap: 8px;
}
.row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--panel-2);
}
.row-old {
  min-width: 0;
}
.old-name {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.old-sub {
  color: var(--muted);
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.row-arrow {
  color: var(--muted);
}
.select {
  width: 100%;
  height: 34px;
  padding: 0 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
  color: #1c2533;
}
.empty {
  padding: 20px 0;
}
</style>
