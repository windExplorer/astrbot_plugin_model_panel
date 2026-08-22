import { createApp } from "vue";
import { createRouter, createWebHashHistory } from "vue-router";
import App from "./App.vue";
import DashboardView from "./views/DashboardView.vue";
import DetectView from "./views/DetectView.vue";
import DefaultModelView from "./views/DefaultModelView.vue";
import CompanionReplaceView from "./views/CompanionReplaceView.vue";

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", name: "dashboard", component: DashboardView, meta: { title: "控制台" } },
    { path: "/detect", name: "detect", component: DetectView, meta: { title: "模型检测排序" } },
    { path: "/default", name: "default", component: DefaultModelView, meta: { title: "默认模型" } },
    { path: "/companion", name: "companion", component: CompanionReplaceView, meta: { title: "伴侣插件替换" } },
  ],
});

createApp(App).use(router).mount("#app");
