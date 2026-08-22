<template>
  <div>
    <div class="card">
      <div class="card-title">AstrBot 当前默认模型</div>
      <div v-if="defaultId" class="value">{{ defaultId }}</div>
      <div v-else class="muted">未设置默认模型</div>
      <p class="muted note">当前为只读展示。切换默认模型的写入能力将在后续版本提供。</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { apiGet } from "../api";

const defaultId = ref("");

onMounted(async () => {
  try {
    const data = await apiGet<{ default_provider_id: string }>("/panel/default_model");
    defaultId.value = data.default_provider_id || "";
  } catch (e) {
    console.error("加载默认模型失败", e);
  }
});
</script>

<style scoped>
.value {
  font-size: 20px;
  font-weight: 700;
  color: var(--accent-2);
}
.note {
  margin-top: 12px;
  font-size: 13px;
}
</style>
