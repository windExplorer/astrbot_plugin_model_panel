<template>
  <div>
    <div class="stat-grid">
      <div class="stat-card">
        <div class="stat-num">{{ overview?.total ?? "–" }}</div>
        <div class="stat-label">LLM 模型数</div>
      </div>
      <div class="stat-card">
        <div class="stat-num" :class="overview?.default_set ? 'tag-ok' : 'tag-warn'">
          {{ overview?.default_set ? "已设置" : "未命中" }}
        </div>
        <div class="stat-label">默认模型</div>
      </div>
      <div class="stat-card">
        <div class="stat-num" :class="overview?.companion_loaded ? 'tag-ok' : 'tag-err'">
          {{ overview?.companion_loaded ? "已加载" : "未加载" }}
        </div>
        <div class="stat-label">伴侣插件</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ overview?.companion_provider_count ?? "–" }}</div>
        <div class="stat-label">伴侣已配置模型</div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">当前默认模型</div>
      <div v-if="overview && overview.default_provider_id" class="muted">
        {{ overview.default_provider_id }}
        <span v-if="!overview.default_set" class="tag-warn">（当前 provider 列表中未命中）</span>
      </div>
      <div v-else class="muted">未设置</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { apiGet, Overview } from "../api";

const overview = ref<Overview | null>(null);

onMounted(async () => {
  try {
    overview.value = await apiGet<Overview>("/panel/overview");
  } catch (e) {
    console.error("加载总览失败", e);
  }
});
</script>

<style scoped>
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px;
  margin-bottom: 18px;
}
.stat-card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 18px;
}
.stat-num {
  font-size: 26px;
  font-weight: 700;
}
.stat-label {
  color: var(--muted);
  font-size: 13px;
  margin-top: 4px;
}
</style>
