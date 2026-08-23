// 主题 store：localStorage 持久化，提供深浅主题切换。
import { ref, watch } from "vue";

export type ThemeMode = "light" | "dark";
const STORAGE_KEY = "model_panel.theme";

function detectInitial(): ThemeMode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    /* ignore */
  }
  try {
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
      return "light";
    }
  } catch {
    /* ignore */
  }
  return "dark";
}

const theme = ref<ThemeMode>(detectInitial());

function applyToDom(mode: ThemeMode) {
  try {
    const root = document.documentElement;
    root.setAttribute("data-theme", mode);
    root.style.colorScheme = mode;
  } catch {
    /* ignore */
  }
}

applyToDom(theme.value);

watch(theme, (mode) => {
  applyToDom(mode);
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    /* ignore */
  }
});

export function useTheme() {
  return {
    theme,
    setTheme(mode: ThemeMode) {
      theme.value = mode;
    },
    toggleTheme() {
      theme.value = theme.value === "dark" ? "light" : "dark";
    },
  };
}
