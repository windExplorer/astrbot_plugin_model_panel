<template>
  <n-config-provider :theme="naiveTheme" :theme-overrides="themeOverrides" :locale="naiveLocale" :date-locale="naiveDateLocale">
    <n-message-provider>
      <n-dialog-provider>
        <div class="layout" :data-theme="theme">
          <aside class="sidebar">
            <div class="brand">
              <span class="brand-dot"></span>
              <span class="brand-name">{{ t("common.appName") }}</span>
            </div>
            <nav class="nav">
              <RouterLink
                v-for="item in navItems"
                :key="item.to"
                :to="item.to"
                class="nav-item"
              >
                {{ t(item.titleKey) }}
              </RouterLink>
            </nav>
            <div class="sidebar-footer">
              <div class="footer-row">
                <n-tooltip :delay="300">
                  <template #trigger>
                    <button class="icon-btn" :aria-label="t('common.theme')" @click="toggleTheme">
                      <span v-if="theme === 'dark'">🌙</span>
                      <span v-else>☀️</span>
                    </button>
                  </template>
                  {{ theme === "dark" ? t("common.themeDark") : t("common.themeLight") }}
                </n-tooltip>
                <n-dropdown :options="langOptions" trigger="click" @select="onSelectLang">
                  <button class="icon-btn lang-btn" :aria-label="t('common.language')">
                    <span>{{ langLabel }}</span>
                    <span class="caret">▾</span>
                  </button>
                </n-dropdown>
              </div>
              <div class="version">{{ t("common.version") }} {{ version }}</div>
            </div>
          </aside>
          <main class="content">
            <header class="content-head">
              <h1>{{ title }}</h1>
            </header>
            <RouterView />
          </main>
        </div>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<script setup lang="ts">
import { computed, h } from "vue";
import { useRoute } from "vue-router";
import {
  darkTheme,
  dateZhCN,
  dateEnUS,
  dateJaJP,
  dateKoKR,
  lightTheme,
  zhCN,
  enUS,
  jaJP,
  koKR,
  NConfigProvider,
  NDialogProvider,
  NDropdown,
  NMessageProvider,
  NTooltip,
} from "naive-ui";

import { useI18n } from "vue-i18n";
import { PLUGIN_VERSION } from "./version";
import { useTheme } from "./stores/theme";
import { useLocale, type AppLocale } from "./stores/locale";

const route = useRoute();
const { t, locale } = useI18n();
const { theme, toggleTheme } = useTheme();
const { locale: localeStore, setLocale, supported } = useLocale();

const version = PLUGIN_VERSION;
const navItems = [
  { to: "/", titleKey: "nav.dashboard" },
  { to: "/detect", titleKey: "nav.detect" },
  { to: "/default", titleKey: "nav.defaultModel" },
  { to: "/companion", titleKey: "nav.companion" },
];

const title = computed(() => {
  const k = route.meta.titleKey as string | undefined;
  return k ? t(k) : t("common.appName");
});

const naiveTheme = computed(() => (theme.value === "dark" ? darkTheme : lightTheme));
const naiveLocale = computed(() => {
  switch (localeStore.value) {
    case "en":
      return enUS;
    case "ja":
      return jaJP;
    case "ko":
      return koKR;
    default:
      return zhCN;
  }
});
const naiveDateLocale = computed(() => {
  switch (localeStore.value) {
    case "en":
      return dateEnUS;
    case "ja":
      return dateJaJP;
    case "ko":
      return dateKoKR;
    default:
      return dateZhCN;
  }
});

// 主题微调：背景色与原项目接近，避免硬切换时跳变
const themeOverrides = computed(() => ({
  common: {
    primaryColor: "#4f7dff",
    primaryColorHover: "#6ea0ff",
    primaryColorPressed: "#3b66e0",
    primaryColorSuppl: "#6ea0ff",
    fontFamily:
      '-apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif',
  },
}));

// 语言切换
const langOptions = supported.map((l) => ({
  key: l,
  label: ({ zh: "中文", en: "English", ja: "日本語", ko: "한국어" } as Record<AppLocale, string>)[l],
}));
const langLabel = computed(
  () => ({ zh: "中文", en: "EN", ja: "JA", ko: "KO" } as Record<AppLocale, string>)[localeStore.value],
);
function onSelectLang(key: string) {
  if ((supported as string[]).includes(key)) {
    setLocale(key as AppLocale);
    locale.value = key as any;
  }
}
</script>

<style>
:root,
:root[data-theme="dark"] {
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
:root[data-theme="light"] {
  --bg: #f4f6fb;
  --panel: #ffffff;
  --panel-2: #f4f6fb;
  --border: #e0e6f0;
  --text: #1f2638;
  --muted: #6b7385;
  --accent: #2f6bff;
  --accent-2: #4f7dff;
  --ok: #19a974;
  --err: #e0444c;
  --warn: #d68900;
}

* {
  box-sizing: border-box;
}
html,
body,
#app {
  height: 100%;
  min-height: 100vh;
  margin: 0;
}
body {
  font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  background: var(--bg);
  color: var(--text);
  transition: background-color 0.2s ease;
}
.layout {
  display: flex;
  min-height: 100vh;
  height: 100vh;
}
.sidebar {
  width: 220px;
  flex-shrink: 0;
  padding: 18px 14px;
  border-right: 1px solid var(--border);
  background: var(--panel);
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 100vh;
  overflow-y: auto;
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
.sidebar-footer {
  margin-top: auto;
  padding-top: 12px;
}
.footer-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px 8px;
}
.icon-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-2);
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
}
.icon-btn:hover {
  filter: brightness(1.1);
}
.lang-btn .caret {
  font-size: 10px;
  color: var(--muted);
}
.version {
  padding: 4px 8px 4px;
  color: var(--muted);
  font-size: 12px;
}
.content {
  flex: 1;
  min-width: 0;
  height: 100vh;
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
.muted {
  color: var(--muted);
}
</style>
