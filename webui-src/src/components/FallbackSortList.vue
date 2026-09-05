<template>
  <div class="fallback-sort">
    <div class="fs-cols">
      <!-- 左：可选模型，点击添加 -->
      <div class="fs-col">
        <div class="fs-title">{{ t("defaultModel.fallbackAvailable") }}</div>
        <n-input
          v-model:value="keyword"
          size="small"
          clearable
          :placeholder="t('defaultModel.fallbackSearch')"
          class="fs-search"
        />
        <div class="fs-list">
          <div
            v-for="opt in filteredAvailable"
            :key="opt.id"
            class="fs-item fs-add"
            :title="label(opt)"
            @click="add(opt.id)"
          >
            <span class="fs-name">{{ label(opt) }}</span>
            <span class="fs-plus">+</span>
          </div>
          <div v-if="!filteredAvailable.length" class="fs-empty">
            {{ t("defaultModel.fallbackNoAvailable") }}
          </div>
        </div>
      </div>

      <!-- 右：已选回退顺序，可拖拽排序 -->
      <div class="fs-col">
        <div class="fs-title">
          {{ t("defaultModel.fallbackSelected") }}
          <span class="fs-count">({{ items.length }})</span>
        </div>
        <div
          class="fs-list"
          @dragover.prevent="onListDragOver"
          @drop.prevent="onListDrop"
        >
          <div
            v-for="(id, idx) in items"
            :key="id"
            class="fs-item"
            :class="{
              dragging: dragIndex === idx,
              'drag-over': overIndex === idx && dragIndex !== idx,
            }"
            draggable="true"
            @dragstart="onDragStart(idx, $event)"
            @dragover.prevent="onDragOver(idx)"
            @drop.stop.prevent="onDrop(idx)"
            @dragend="onDragEnd"
          >
            <span class="fs-handle" :title="t('defaultModel.fallbackDragHint')"
              >⋮⋮</span
            >
            <span class="fs-index">{{ idx + 1 }}</span>
            <span class="fs-name" :title="labelById(id)">{{ labelById(id) }}</span>
            <span class="fs-ops">
              <n-button
                text
                size="tiny"
                :disabled="idx === 0"
                :title="t('defaultModel.fallbackMoveUp')"
                @click="moveUp(idx)"
                >↑</n-button
              >
              <n-button
                text
                size="tiny"
                :disabled="idx === items.length - 1"
                :title="t('defaultModel.fallbackMoveDown')"
                @click="moveDown(idx)"
                >↓</n-button
              >
              <n-button
                text
                size="tiny"
                type="error"
                :title="t('defaultModel.fallbackRemove')"
                @click="remove(idx)"
                >✕</n-button
              >
            </span>
          </div>
          <div v-if="!items.length" class="fs-empty">
            {{ t("defaultModel.fallbackEmpty") }}
          </div>
        </div>
      </div>
    </div>
    <div class="fs-hint">
      <n-text depth="3">{{ t("defaultModel.fallbackHint") }}</n-text>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { NButton, NInput, NText } from "naive-ui";

import type { DefaultModelOption } from "../api";

const props = defineProps<{
  options: DefaultModelOption[];
  /** 已选 provider id，数组顺序即回退优先级顺序 */
  modelValue: string[];
}>();
const emit = defineEmits<{ "update:modelValue": [string[]] }>();

const { t } = useI18n();
const keyword = ref("");

const items = computed(() => props.modelValue || []);

function label(opt: DefaultModelOption): string {
  return opt.model ? `${opt.name} · ${opt.model}` : opt.name || opt.id;
}

function labelById(id: string): string {
  const hit = (props.options || []).find((o) => o.id === id);
  return hit ? label(hit) : id;
}

const filteredAvailable = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  return (props.options || []).filter(
    (o) =>
      !items.value.includes(o.id) && (!kw || label(o).toLowerCase().includes(kw)),
  );
});

function emitNext(next: string[]) {
  emit("update:modelValue", next);
}

function add(id: string) {
  if (items.value.includes(id)) return;
  emitNext([...items.value, id]);
}

function remove(idx: number) {
  const next = [...items.value];
  next.splice(idx, 1);
  emitNext(next);
}

function moveUp(idx: number) {
  if (idx <= 0) return;
  const next = [...items.value];
  [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
  emitNext(next);
}

function moveDown(idx: number) {
  if (idx >= items.value.length - 1) return;
  const next = [...items.value];
  [next[idx + 1], next[idx]] = [next[idx], next[idx + 1]];
  emitNext(next);
}

// ---------------- 拖拽排序（原生 HTML5 DnD，不引入额外依赖） ----------------
const dragIndex = ref<number | null>(null);
const overIndex = ref<number | null>(null);

function onDragStart(idx: number, e: DragEvent) {
  dragIndex.value = idx;
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = "move";
    // Firefox 必须设置数据，否则不会触发后续拖拽事件
    e.dataTransfer.setData("text/plain", String(idx));
  }
}

function onDragOver(idx: number) {
  overIndex.value = idx;
}

function onDrop(idx: number) {
  const from = dragIndex.value;
  if (from === null || from === idx) {
    reset();
    return;
  }
  const next = [...items.value];
  const [moved] = next.splice(from, 1);
  // 插入指示线画在目标项上边框，语义是「插到该项之前」；
  // 删除 from 之后其后的元素会前移一位，所以 from < idx 时目标索引要减 1
  const target = from < idx ? idx - 1 : idx;
  next.splice(target, 0, moved);
  emitNext(next);
  reset();
}

// 拖到列表空白处：移动到末尾
function onListDragOver() {
  overIndex.value = null;
}

function onListDrop() {
  const from = dragIndex.value;
  if (from === null || from >= items.value.length - 1) {
    reset();
    return;
  }
  const next = [...items.value];
  const [moved] = next.splice(from, 1);
  next.push(moved);
  emitNext(next);
  reset();
}

function onDragEnd() {
  reset();
}

function reset() {
  dragIndex.value = null;
  overIndex.value = null;
}
</script>

<style scoped>
.fallback-sort {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.fs-cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.fs-col {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.fs-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  margin-bottom: 6px;
}
.fs-count {
  font-weight: 400;
}
.fs-search {
  margin-bottom: 6px;
}
.fs-list {
  border: 1px solid rgba(128, 128, 128, 0.18);
  border-radius: 8px;
  padding: 4px;
  min-height: 152px;
  max-height: 244px;
  overflow-y: auto;
  background: rgba(128, 128, 128, 0.04);
}
.fs-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  font-size: 13px;
  border-top: 2px solid transparent;
  transition: background 0.15s ease;
}
.fs-item + .fs-item {
  margin-top: 2px;
}
.fs-add {
  cursor: pointer;
}
.fs-add:hover {
  background: rgba(128, 128, 128, 0.14);
}
.fs-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fs-plus {
  color: var(--muted);
  flex: none;
  font-size: 14px;
}
.fs-handle {
  cursor: grab;
  color: var(--muted);
  flex: none;
  user-select: none;
  letter-spacing: -2px;
  font-size: 13px;
}
.fs-handle:active {
  cursor: grabbing;
}
.fs-index {
  flex: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--accent-2);
  color: #fff;
  font-size: 11px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.fs-ops {
  flex: none;
  display: flex;
  gap: 2px;
}
.fs-item.dragging {
  opacity: 0.4;
}
/* 拖拽插入位置指示线：落在哪一项上，就插到它前面 */
.fs-item.drag-over {
  border-top-color: var(--accent-2);
}
.fs-empty {
  padding: 18px 8px;
  text-align: center;
  color: var(--muted);
  font-size: 12px;
}
.fs-hint {
  font-size: 12px;
}
@media (max-width: 720px) {
  .fs-cols {
    grid-template-columns: 1fr;
  }
}
</style>
