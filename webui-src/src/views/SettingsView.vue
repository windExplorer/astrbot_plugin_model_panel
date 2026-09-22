<template>
  <div class="settings-view">
    <n-card :title="t('settings.title')" size="small">
      <n-spin :show="loading">
        <n-form
          v-if="form"
          label-placement="left"
          label-width="auto"
          :model="form"
          size="small"
        >
          <n-form-item :label="t('settings.testTimeout')">
            <n-input-number
              v-model:value="form.test_timeout"
              :min="1"
              :max="600"
              :step="5"
              style="width: 200px"
            />
            <n-text depth="3" style="margin-left: 12px; font-size: 12px">
              {{ t("settings.testTimeoutHint") }}
            </n-text>
          </n-form-item>

          <n-form-item :label="t('settings.testRetryCount')">
            <n-input-number
              v-model:value="form.test_retry_count"
              :min="0"
              :max="10"
              :step="1"
              style="width: 200px"
            />
            <n-text depth="3" style="margin-left: 12px; font-size: 12px">
              {{ t("settings.testRetryCountHint") }}
            </n-text>
          </n-form-item>

          <n-form-item :label="t('settings.testRetryBackoff')">
            <n-input-number
              v-model:value="form.test_retry_backoff"
              :min="0"
              :max="60"
              :step="0.5"
              style="width: 200px"
            />
            <n-text depth="3" style="margin-left: 12px; font-size: 12px">
              {{ t("settings.testRetryBackoffHint") }}
            </n-text>
          </n-form-item>

          <n-form-item :label="t('settings.historyRetentionDays')">
            <n-input-number
              v-model:value="form.history_retention_days"
              :min="0"
              :max="3650"
              :step="1"
              style="width: 200px"
            />
            <n-text depth="3" style="margin-left: 12px; font-size: 12px">
              {{ t("settings.historyRetentionDaysHint") }}
            </n-text>
          </n-form-item>

          <!-- 卡片外观：文案直接写中文（本仓库约定不再新增多语言文案） -->
          <n-form-item label="卡片配色主题">
            <n-select
              v-model:value="form.card_theme"
              :options="themeOptions"
              style="width: 200px"
            />
            <n-text depth="3" style="margin-left: 12px; font-size: 12px">
              聊天里那几张图片卡片（模型状态 / 检测结果 / 告警）的配色，不影响本页。
            </n-text>
          </n-form-item>

          <n-form-item label="卡片中文字体路径">
            <n-input
              v-model:value="form.card_font_path"
              placeholder="留空 = 自动探测系统字体"
              style="width: 360px"
              clearable
            />
            <n-text depth="3" style="margin-left: 12px; font-size: 12px">
              找不到字体时卡片会退化成纯文本，可在这里填一个 .ttc / .otf 的绝对路径。
            </n-text>
          </n-form-item>

          <n-form-item>
            <n-space>
              <n-button type="primary" :loading="saving" @click="save">
                {{ t("settings.btnSave") }}
              </n-button>
              <n-button quaternary @click="reload" :disabled="loading">
                {{ t("settings.btnReload") }}
              </n-button>
            </n-space>
          </n-form-item>
        </n-form>
      </n-spin>
    </n-card>

    <n-card :title="t('settings.tipTitle')" size="small" class="mt-3">
      <n-text depth="3" style="display: block; margin-bottom: 8px">
        {{ t("settings.tipLine1") }}
      </n-text>
      <n-text depth="3" style="display: block; margin-bottom: 8px">
        {{ t("settings.tipLine2") }}
      </n-text>
      <n-text depth="3" style="display: block">
        {{ t("settings.tipLine3") }}
      </n-text>
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { useI18n } from "vue-i18n";
import {
  NButton,
  NCard,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSelect,
  NSpace,
  NSpin,
  NText,
  useMessage,
} from "naive-ui";

import { apiGet, apiPost } from "../api";

const { t } = useI18n();
const message = useMessage();

interface Config {
  test_timeout: number;
  test_retry_count: number;
  test_retry_backoff: number;
  history_retention_days: number;
  card_theme: string;
  card_font_path: string;
}

interface ThemeOption {
  value: string;
  label: string;
}

const form = ref<Config | null>(null);
// 主题候选由后端下发（唯一来源是 card_render.THEME_CHOICES）。
// 前端再抄一份的话，以后加主题就会出现「下拉里没有、后台也选不到」。
const themeOptions = ref<ThemeOption[]>([]);
const loading = ref(false);
const saving = ref(false);

async function load() {
  loading.value = true;
  try {
    const r = await apiGet<{ items: Config; themes?: ThemeOption[] }>("/panel/config");
    form.value = { ...r.items };
    themeOptions.value = r.themes?.length ? r.themes : [{ value: "rose", label: "樱粉（默认）" }];
  } catch (e: any) {
    message.error(String(e?.message || e || "加载失败"));
  } finally {
    loading.value = false;
  }
}

async function save() {
  if (!form.value) return;
  saving.value = true;
  try {
    const r = await apiPost<{ ok: boolean; error?: string }>(
      "/panel/config",
      { items: form.value },
    );
    if (r.ok) {
      message.success(t("settings.saved"));
    } else {
      message.error(t("settings.saveFail") + (r.error ? `: ${r.error}` : ""));
    }
  } catch (e: any) {
    message.error(String(e?.message || e));
  } finally {
    saving.value = false;
  }
}

async function reload() {
  await load();
  message.info(t("settings.reloaded"));
}

onMounted(() => {
  load();
});
</script>

<style scoped>
.settings-view {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.mt-3 {
  margin-top: 12px;
}
</style>
