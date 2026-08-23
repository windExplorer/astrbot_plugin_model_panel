// i18n 模块入口：默认中文，加载四份 locale，按需回退到中文。
import { createI18n } from "vue-i18n";
import { useLocale } from "../stores/locale";

import zh from "../locales/zh.json";
import en from "../locales/en.json";
import ja from "../locales/ja.json";
import ko from "../locales/ko.json";

const { locale } = useLocale();

export const i18n = createI18n({
  legacy: false,
  globalInjection: true,
  locale: locale.value,
  fallbackLocale: "zh",
  messages: {
    zh,
    en,
    ja,
    ko,
  },
  // 缺 key 时回退到 key 字符串，调试期更明显
  missing: (_locale, key) => key,
});

// 跟随 store 切换 locale
import { watch } from "vue";
watch(locale, (v) => {
  i18n.global.locale.value = v as any;
});
