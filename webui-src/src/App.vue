<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-dot"></span>
        <span class="brand-name">模型控制台</span>
      </div>
      <nav class="nav">
        <RouterLink v-for="item in navItems" :key="item.to" :to="item.to" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
      <div class="version">{{ version }}</div>
    </aside>
    <main class="content">
      <header class="content-head">
        <h1>{{ title }}</h1>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import { PLUGIN_VERSION } from "./version";

const route = useRoute();
const version = PLUGIN_VERSION;
const navItems = [
  { to: "/", label: "控制台" },
  { to: "/detect", label: "模型检测排序" },
  { to: "/default", label: "默认模型" },
  { to: "/companion", label: "伴侣插件替换" },
];
const title = computed(() => (route.meta.title as string) || "模型控制台");
</script>

<style>
:root {
  --bg: #0f1626;
  --panel: #1a2337;
  --panel-2: #202c44;
  --border: #2c3a56;
  --text: #e6ebf4;
  --muted: #8a97ad;
  --accent: #4f7dff;
  --accent-2: #6ea0ff;
  --ok: #34c98a;
  --err: #ff6b6b;
  --warn: #ffb454;
}
* {
  box-sizing: border-box;
}
html,
body,
#app {
  height: 100%;
  margin: 0;
}
body {
  font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  background: var(--bg);
  color: var(--text);
}
.layout {
  display: flex;
  height: 100%;
}
.sidebar {
  width: 220px;
  flex-shrink: 0;
  padding: 18px 14px;
  border-right: 1px solid var(--border);
  background: var(--panel);
  display: flex;
  flex-direction: column;
}
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px 18px;
}
.brand-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
}
.brand-name {
  font-weight: 700;
  font-size: 16px;
}
.nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 6px;
}
.nav-item {
  display: block;
  padding: 10px 12px;
  border-radius: 8px;
  color: var(--muted);
  text-decoration: none;
  font-size: 14px;
  transition: background 0.15s, color 0.15s;
}
.nav-item:hover {
  background: var(--panel-2);
  color: var(--text);
}
.nav-item.router-link-active {
  background: rgba(79, 125, 255, 0.16);
  color: var(--accent-2);
  font-weight: 600;
}
.version {
  margin-top: auto;
  padding: 10px 8px 4px;
  color: var(--muted);
  font-size: 12px;
}
.content {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 24px 28px;
}
.content-head h1 {
  margin: 0 0 20px;
  font-size: 22px;
}
.card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 18px 20px;
  margin-bottom: 16px;
}
.card-title {
  font-size: 15px;
  font-weight: 600;
  margin: 0 0 12px;
  color: var(--text);
}
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-2);
  color: var(--text);
  font-size: 14px;
  cursor: pointer;
  transition: filter 0.15s;
}
.btn:hover {
  filter: brightness(1.15);
}
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.btn-primary {
  background: var(--accent);
  border-color: transparent;
  color: #fff;
}
.btn-danger {
  background: rgba(255, 107, 107, 0.18);
  border-color: transparent;
  color: var(--err);
}
.btn-sm {
  padding: 5px 10px;
  font-size: 13px;
}
.tag-ok {
  color: var(--ok);
}
.tag-err {
  color: var(--err);
}
.tag-warn {
  color: var(--warn);
}
.muted {
  color: var(--muted);
}
</style>
