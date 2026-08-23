<template>
  <n-config-provider :theme="naiveTheme" :theme-overrides="themeOverrides" :locale="naiveLocale" :date-locale="naiveDateLocale">
    <n-message-provider>
      <n-dialog-provider>
        <div class="layout" :data-theme="theme">
          <!-- 左侧菜单：sticky 不随主区滚动 -->
          <aside class="sidebar">
            <div class="brand">
              <img class="brand-logo" :src="logoUrl" alt="logo" />
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
                    <n-button quaternary circle size="small" :aria-label="t('common.theme')" @click="toggleTheme">
                      <template #icon>
                        <span style="font-size: 16px">{{ theme === "dark" ? "🌙" : "☀️" }}</span>
                      </template>
                    </n-button>
                  </template>
                  {{ theme === "dark" ? t("common.themeDark") : t("common.themeLight") }}
                </n-tooltip>
                <n-dropdown :options="langOptions" trigger="click" @select="onSelectLang">
                  <n-button quaternary size="small">
                    {{ langLabel }}
                    <template #icon>
                      <span style="font-size: 10px">▾</span>
                    </template>
                  </n-button>
                </n-dropdown>
              </div>
              <div class="version">{{ t("common.version") }} {{ version }}</div>
            </div>
          </aside>

          <!-- 主区：header 固定在顶部，内容卡片可滚动 -->
          <main class="content">
            <header class="content-head">
              <h1>{{ title }}</h1>
            </header>
            <div class="content-body">
              <RouterView />
            </div>
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
  NButton,
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
import logoUrl from "./assets/logo.jpg";

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
  { to: "/settings", titleKey: "nav.settings" },
];

const title = computed(() => {
  const k = route.meta.titleKey as string | undefined;
  return k ? t(k) : t("common.appName");
});

const naiveTheme = computed(() => (theme.value === "dark" ? darkTheme : lightTheme));
const naiveLocale = computed(() => {
  switch (localeStore.value) {
    case "en": return enUS;
    case "ja": return jaJP;
    case "ko": return koKR;
    default: return zhCN;
  }
});
const naiveDateLocale = computed(() => {
  switch (localeStore.value) {
    case "en": return dateEnUS;
    case "ja": return dateJaJP;
    case "ko": return dateKoKR;
    default: return dateZhCN;
  }
});

const themeOverrides = computed(() => ({
  common: {
    primaryColor: "#ff7eb6",
    primaryColorHover: "#ff9cc6",
    primaryColorPressed: "#e05f9d",
    primaryColorSuppl: "#ff9cc6",
    borderRadius: "12px",
    borderRadiusSmall: "10px",
    fontFamily:
      '-apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif',
  },
}));

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
/* 主题色：深 / 浅 */
:root,
:root[data-theme="dark"] {
  --bg: #1a1430;
  --panel: #241d38;
  --panel-2: #2e2550;
  --border: #3a2f58;
  --text: #f1e8f5;
  --muted: #b3a3c9;
  --accent: #ff7eb6;
  --accent-2: #c49bff;
  --ok: #5ad1a8;
  --err: #ff7d8f;
  --warn: #ffc56e;
}
:root[data-theme="light"] {
  --bg: #fff5f9;
  --panel: #ffffff;
  --panel-2: #fff0f6;
  --border: #ffdce9;
  --text: #3d2b3f;
  --muted: #9a7f9a;
  --accent: #ff5d9e;
  --accent-2: #b47bff;
  --ok: #3fae84;
  --err: #ef4d68;
  --warn: #e59a2b;
}

* {
  box-sizing: border-box;
}
html,
body,
#app {
  height: 100%;
  margin: 0;
  overflow: hidden;
}
body {
  font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  background: var(--bg);
  color: var(--text);
  transition: background-color 0.2s ease;
}

/* 整体布局：flex 横排 + 全屏高度 */
.layout {
  display: flex;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
}

/* 侧边栏：固定在左侧，内部独立滚动（自身可滚动但不参与主区滚动） */
.sidebar {
  width: 220px;
  flex-shrink: 0;
  padding: 18px 14px;
  border-right: 1px solid var(--border);
  background: var(--panel);
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow-y: auto;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px 18px;
}
.brand-logo {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  object-fit: cover;
  box-shadow: 0 0 0 2px var(--border), 0 4px 10px rgba(255, 125, 182, 0.35);
  flex-shrink: 0;
}
.brand-name {
  font-weight: 800;
  font-size: 16px;
  letter-spacing: 0.5px;
  background: linear-gradient(90deg, var(--accent), var(--accent-2));
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
.nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 6px;
}
.nav-item {
  display: block;
  padding: 10px 14px;
  border-radius: 12px;
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
  background: linear-gradient(135deg, rgba(255, 125, 182, 0.18), rgba(180, 123, 255, 0.18));
  color: var(--accent);
  font-weight: 700;
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
.version {
  padding: 4px 8px 4px;
  color: var(--muted);
  font-size: 12px;
}

/* 主区：整列 flex 纵向；header sticky 不滚动，content-body 独立滚动 */
.content {
  flex: 1;
  min-width: 0;
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  /* 柔和萌系渐变背景 */
  background: radial-gradient(1200px 600px at 90% -10%, rgba(255, 125, 182, 0.10), transparent 60%),
    radial-gradient(1000px 500px at -10% 110%, rgba(180, 123, 255, 0.10), transparent 55%),
    var(--bg);
}
.content-head {
  flex-shrink: 0;
  padding: 18px 28px 12px;
  background: var(--bg);
  border-bottom: 1px solid var(--border);
  z-index: 5;
}
.content-head h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
}
.content-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 16px 28px 28px;
}
</style>
