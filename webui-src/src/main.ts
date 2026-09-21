import { createApp } from "vue";
import { createRouter, createWebHashHistory } from "vue-router";

import App from "./App.vue";
import DashboardView from "./views/DashboardView.vue";
import DetectView from "./views/DetectView.vue";
import MonitorView from "./views/MonitorView.vue";
import ProfileView from "./views/ProfileView.vue";
import DefaultModelView from "./views/DefaultModelView.vue";
import CompanionReplaceView from "./views/CompanionReplaceView.vue";
import PluginsView from "./views/PluginsView.vue";
import SettingsView from "./views/SettingsView.vue";

import { i18n } from "./i18n";
import { FAVICON_BASE64 } from "./assets/faviconBase64";

// AstrBot 页面只加载 JS bundle，静态图片资源无法用相对路径引用，
// 故 favicon 也改用 base64 内嵌。
(function setFavicon() {
  try {
    let link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
    if (!link) {
      link = document.createElement("link");
      link.rel = "icon";
      document.head.appendChild(link);
    }
    link.href = FAVICON_BASE64;
  } catch { /* ignore */ }
})();

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", name: "dashboard", component: DashboardView, meta: { titleKey: "nav.dashboard" } },
    { path: "/detect", name: "detect", component: DetectView, meta: { titleKey: "nav.detect" } },
    { path: "/monitor", name: "monitor", component: MonitorView, meta: { titleKey: "nav.monitor" } },
    { path: "/profile", name: "profile", component: ProfileView, meta: { titleKey: "nav.profile" } },
    { path: "/default", name: "default", component: DefaultModelView, meta: { titleKey: "nav.defaultModel" } },
    { path: "/companion", name: "companion", component: CompanionReplaceView, meta: { titleKey: "nav.companion" } },
    { path: "/plugins", name: "plugins", component: PluginsView, meta: { titleKey: "nav.plugins" } },
    { path: "/settings", name: "settings", component: SettingsView, meta: { titleKey: "nav.settings" } },
  ],
});

const app = createApp(App);
app.use(router);
app.use(i18n);
app.mount("#app");
