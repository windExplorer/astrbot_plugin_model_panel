<template>
  <div class="default-view">
    <n-card :title="t('defaultModel.title')" size="small">
      <template #header-extra>
        <n-button quaternary size="small" :loading="refreshing" @click="manualRefresh">
          {{ t("detect.btnRefresh") }}
        </n-button>
      </template>

      <div v-if="loaded === false" class="empty">
        <n-text depth="3">{{ t("defaultModel.loadFail") }}</n-text>
      </div>

      <div v-else class="fields">
        <div class="field">
          <div class="label">{{ t("defaultModel.chat") }}</div>
          <n-select
            v-model:value="chatId"
            :options="options"
            filterable
            clearable
            :placeholder="t('defaultModel.chatPlaceholder')"
          />
          <div class="hint">
            <n-text depth="3">{{ t("defaultModel.chatHint") }}</n-text>
          </div>
        </div>

        <div class="field">
          <div class="label">{{ t("defaultModel.fallback") }}</div>
          <n-select
            v-model:value="fallbackIds"
            :options="options"
            multiple
            filterable
            clearable
            :placeholder="t('defaultModel.fallbackPlaceholder')"
          />
          <div class="hint">
            <n-text depth="3">{{ t("defaultModel.fallbackHint") }}</n-text>
          </div>
        </div>

        <div class="field">
          <div class="label">{{ t("defaultModel.vision") }}</div>
          <n-select
            v-model:value="visionId"
            :options="options"
            filterable
            clearable
            :placeholder="t('defaultModel.visionPlaceholder')"
          />
          <div class="hint">
            <n-text depth="3">{{ t("defaultModel.visionHint") }}</n-text>
          </div>
        </div>

        <div class="actions">
          <n-button type="primary" size="small" :loading="saving" @click="save">
            {{ t("defaultModel.save") }}
          </n-button>
          <n-text v-if="effectiveLabel" depth="3" class="effective">
            {{ t("defaultModel.effective") }}: {{ effectiveLabel }}
          </n-text>
        </div>
      </div>
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { NButton, NCard, NSelect, NText, useMessage } from "naive-ui";

import {
  apiGetDefaultModelConfig,
  apiSetDefaultModel,
  type DefaultModelOption,
} from "../api";

const { t } = useI18n();
const message = useMessage();

const loaded = ref<boolean | null>(null);
const refreshing = ref(false);
const saving = ref(false);

const items = ref<DefaultModelOption[]>([]);
const chatId = ref<string | null>(null);
const fallbackIds = ref<string[]>([]);
const visionId = ref<string | null>(null);
const effectiveId = ref("");

function labelOf(id: string): string {
  const hit = items.value.find((it) => it.id === id);
  if (!hit) return id;
  return hit.model ? `${hit.name} · ${hit.model}` : hit.name || hit.id;
}

const options = computed(() =>
  items.value.map((it) => ({
    label: it.model ? `${it.name} · ${it.model}` : it.name || it.id,
    value: it.id,
  })),
);

const effectiveLabel = computed(() =>
  effectiveId.value ? labelOf(effectiveId.value) : "",
);

async function load() {
  try {
    const data = await apiGetDefaultModelConfig();
    items.value = data.items || [];
    chatId.value = data.chat_provider_id || null;
    fallbackIds.value = data.fallback_provider_ids || [];
    visionId.value = data.vision_provider_id || null;
    effectiveId.value = data.effective_chat_provider_id || "";
    loaded.value = true;
  } catch (e) {
    console.error("加载默认模型配置失败", e);
    loaded.value = false;
  }
}

async function save() {
  if (saving.value) return;
  saving.value = true;
  try {
    const res = await apiSetDefaultModel({
      chat_provider_id: chatId.value ?? "",
      fallback_provider_ids: fallbackIds.value,
      vision_provider_id: visionId.value ?? "",
    });
    if (res.ok) {
      message.success(
        res.no_change ? t("defaultModel.noChange") : t("defaultModel.saved"),
      );
    } else {
      message.error(
        t("defaultModel.saveFail", { msg: res.error ? `: ${res.error}` : "" }),
      );
    }
    await load();
  } catch (e: any) {
    message.error(
      t("defaultModel.saveFail", { msg: e?.message ? `: ${e.message}` : "" }),
    );
  } finally {
    saving.value = false;
  }
}

async function manualRefresh() {
  if (refreshing.value) return;
  refreshing.value = true;
  try {
    await load();
    message.success(t("defaultModel.refreshed"));
  } catch (e) {
    console.error(e);
  } finally {
    refreshing.value = false;
  }
}

function onVisibility() {
  if (document.visibilityState === "visible") {
    load();
  }
}

onMounted(() => {
  load();
  // 切回本页 / 从其它标签页切回时自动刷新，保证展示的是最新配置
  document.addEventListener("visibilitychange", onVisibility);
});

onUnmounted(() => {
  document.removeEventListener("visibilitychange", onVisibility);
});
</script>

<style scoped>
.fields {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.label {
  font-weight: 600;
  margin-bottom: 6px;
}
.hint {
  margin-top: 6px;
  font-size: 12px;
}
.actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.effective {
  font-size: 12px;
}
.empty {
  font-size: 13px;
}
</style>
