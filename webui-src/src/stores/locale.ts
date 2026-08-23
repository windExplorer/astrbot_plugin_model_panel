// 语言 store：默认中文，从 localStorage 读取用户偏好，否则跟随浏览器。
import { ref, watch } from "vue";

export type AppLocale = "zh" | "en" | "ja" | "ko";
const STORAGE_KEY = "model_panel.locale";
const SUPPORTED: AppLocale[] = ["zh", "en", "ja", "ko"];

function detectInitial(): AppLocale {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && (SUPPORTED as string[]).includes(saved)) {
      return saved as AppLocale;
    }
  } catch {
    /* ignore */
  }
  try {
    const lang = (navigator.language || "zh").toLowerCase();
    if (lang.startsWith("zh")) return "zh";
    if (lang.startsWith("ja")) return "ja";
    if (lang.startsWith("ko")) return "ko";
    if (lang.startsWith("en")) return "en";
  } catch {
    /* ignore */
  }
  return "zh";
}

const locale = ref<AppLocale>(detectInitial());

watch(locale, (v) => {
  try {
    localStorage.setItem(STORAGE_KEY, v);
  } catch {
    /* ignore */
  }
  try {
    document.documentElement.setAttribute("lang", v);
  } catch {
    /* ignore */
  }
});

try {
  document.documentElement.setAttribute("lang", locale.value);
} catch {
  /* ignore */
}

export function useLocale() {
  return {
    locale,
    setLocale(v: AppLocale) {
      locale.value = v;
    },
    supported: SUPPORTED,
  };
}
