import { createApp } from "vue";
import { createRouter, createWebHashHistory } from "vue-router";

import App from "./App.vue";
import DashboardView from "./views/DashboardView.vue";
import DetectView from "./views/DetectView.vue";
import DefaultModelView from "./views/DefaultModelView.vue";
import CompanionReplaceView from "./views/CompanionReplaceView.vue";
import SettingsView from "./views/SettingsView.vue";

import { i18n } from "./i18n";

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", name: "dashboard", component: DashboardView, meta: { titleKey: "nav.dashboard" } },
    { path: "/detect", name: "detect", component: DetectView, meta: { titleKey: "nav.detect" } },
    { path: "/default", name: "default", component: DefaultModelView, meta: { titleKey: "nav.defaultModel" } },
    { path: "/companion", name: "companion", component: CompanionReplaceView, meta: { titleKey: "nav.companion" } },
    { path: "/settings", name: "settings", component: SettingsView, meta: { titleKey: "nav.settings" } },
  ],
});

const app = createApp(App);
app.use(router);
app.use(i18n);
app.mount("#app");
