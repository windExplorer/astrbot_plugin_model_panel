<template>
  <div class="fallback-sort">
    <div class="fs-cols">
      <!-- 左：可选模型，点击切换选中/取消 -->
      <div class="fs-col">
        <div class="fs-head">
          <span class="fs-title">{{ t("defaultModel.fallbackAvailable") }}</span>
          <span class="fs-badge">{{ filtered.length }}</span>
        </div>
        <n-input
          v-model:value="keyword"
          size="small"
          clearable
          :placeholder="t('defaultModel.fallbackSearch')"
          class="fs-search"
        />
        <div class="fs-list">
          <div
            v-for="opt in filtered"
            :key="opt.id"
            class="fs-card selectable"
            :class="{ selected: isSelected(opt.id) }"
            :title="label(opt)"
            @click="toggle(opt.id)"
          >
            <span class="fs-check" :class="{ on: isSelected(opt.id) }">
              {{ isSelected(opt.id) ? orderOf(opt.id) : "" }}
            </span>
            <span class="fs-body">
              <span class="fs-name">{{ titleOf(opt) }}</span>
              <span class="fs-sub">{{ subOf(opt) }}</span>
            </span>
          </div>
          <div v-if="!filtered.length" class="fs-empty">
            {{ t("defaultModel.fallbackNoAvailable") }}
          </div>
        </div>
      </div>

      <!-- 右：回退顺序，整张卡片可拖拽 -->
      <div class="fs-col">
        <div class="fs-head">
          <span class="fs-title">{{ t("defaultModel.fallbackSelected") }}</span>
          <span class="fs-badge accent">{{ items.length }}</span>
        </div>
        <div class="fs-subline">
          <span class="fs-grip-demo"></span>
          <span>{{ t("defaultModel.fallbackDragTip") }}</span>
        </div>
        <div
          class="fs-list"
          @dragover.prevent="onListDragOver"
          @drop.prevent="onListDrop"
        >
          <div
            v-for="(id, idx) in items"
            :key="id"
            class="fs-card sortable"
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
            <span
              class="fs-grip"
              :title="t('defaultModel.fallbackDragHint')"
            ></span>
            <span class="fs-index">{{ idx + 1 }}</span>
            <span class="fs-body">
              <span class="fs-name">{{ nameOf(id) }}</span>
              <span class="fs-sub">{{ subOfById(id) }}</span>
            </span>
            <span class="fs-ops">
              <button
                type="button"
                class="op"
                :disabled="idx === 0"
                :title="t('defaultModel.fallbackMoveUp')"
                :aria-label="t('defaultModel.fallbackMoveUp')"
                @click="moveUp(idx)"
              >
                ↑
              </button>
              <button
                type="button"
                class="op"
                :disabled="idx === items.length - 1"
                :title="t('defaultModel.fallbackMoveDown')"
                :aria-label="t('defaultModel.fallbackMoveDown')"
                @click="moveDown(idx)"
              >
                ↓
              </button>
              <span class="ops-sep"></span>
              <button
                type="button"
                class="op danger"
                :title="t('defaultModel.fallbackRemove')"
                :aria-label="t('defaultModel.fallbackRemove')"
                @click="remove(idx)"
              >
                ✕
              </button>
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
import { NInput, NText } from "naive-ui";

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

function label(o: DefaultModelOption): string {
  const main = o.model || o.name || o.id;
  return o.model && o.name && o.name !== o.model ? `${o.name} · ${o.model}` : main;
}
/** 卡片主标题：优先模型名 */
function titleOf(o: DefaultModelOption): string {
  return o.model || o.name || o.id;
}
/** 卡片副标题：供应商名；没有模型名时退回 id */
function subOf(o: DefaultModelOption): string {
  if (o.model && o.name && o.name !== o.model) return o.name;
  return o.model ? o.id : "";
}

function optOf(id: string): DefaultModelOption | undefined {
  return (props.options || []).find((o) => o.id === id);
}
function nameOf(id: string): string {
  const o = optOf(id);
  return o ? titleOf(o) : id;
}
function subOfById(id: string): string {
  const o = optOf(id);
  return o ? subOf(o) : "";
}

/** 左侧不再过滤掉已选项，只按关键字过滤；已选项以选中态展示 */
const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  if (!kw) return props.options || [];
  return (props.options || []).filter((o) => label(o).toLowerCase().includes(kw));
});

function isSelected(id: string): boolean {
  return items.value.includes(id);
}
/** 该模型在回退链中的序号（1 起） */
function orderOf(id: string): number {
  return items.value.indexOf(id) + 1;
}

function emitNext(next: string[]) {
  emit("update:modelValue", next);
}

/** 点击左侧卡片：选中加入右侧 / 取消选中并从右侧移除 */
function toggle(id: string) {
  if (isSelected(id)) {
    emitNext(items.value.filter((x) => x !== id));
  } else {
    emitNext([...items.value, id]);
  }
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
  // 插入指示线画在目标卡片上方，语义是「插到它前面」；
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
  gap: 10px;
}

/* 两栏等高：grid 默认 stretch，内部列表用 flex:1 撑满 */
.fs-cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  align-items: stretch;
}
.fs-col {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.fs-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 20px;
  margin-bottom: 8px;
}
.fs-title {
  font-size: 13px;
  font-weight: 700;
}
.fs-badge {
  font-size: 11px;
  line-height: 18px;
  padding: 0 8px;
  border-radius: 999px;
  background: rgba(128, 128, 128, 0.18);
  color: var(--muted);
  font-weight: 600;
}
.fs-badge.accent {
  background: var(--accent-2);
  color: #fff;
}

.fs-search {
  margin-bottom: 8px;
}
/* 与搜索框同高，保证左右两栏列表顶部对齐 */
.fs-subline {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--muted);
}

.fs-list {
  flex: 1;
  min-height: 288px;
  max-height: 420px;
  overflow-y: auto;
  border: 1px solid rgba(128, 128, 128, 0.18);
  border-radius: 10px;
  padding: 8px;
  background: rgba(128, 128, 128, 0.04);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* ---------- 模型卡片 ---------- */
.fs-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid rgba(128, 128, 128, 0.22);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.02);
  transition: border-color 0.15s, box-shadow 0.15s, transform 0.15s,
    background 0.15s;
}

/* 左侧：可点选 */
.fs-card.selectable {
  cursor: pointer;
}
.fs-card.selectable:hover {
  border-color: rgba(128, 128, 128, 0.45);
  background: rgba(128, 128, 128, 0.09);
}
.fs-card.selected {
  border-color: var(--accent-2);
  box-shadow: 0 0 0 1px var(--accent-2) inset;
}
.fs-card.selected::after {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: 10px;
  background: var(--accent-2);
  opacity: 0.1;
  pointer-events: none;
}

/* 右侧：可拖拽 */
.fs-card.sortable {
  cursor: grab;
  background: rgba(128, 128, 128, 0.07);
}
.fs-card.sortable:hover {
  border-color: var(--accent-2);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.08);
}
.fs-card.sortable:active {
  cursor: grabbing;
}
.fs-card.dragging {
  opacity: 0.45;
  transform: scale(0.99);
}
/* 拖拽插入位置指示线：落在哪张卡片上，就插到它前面 */
.fs-card.drag-over::before {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  top: -5px;
  height: 3px;
  border-radius: 2px;
  background: var(--accent-2);
}

/* 选中标记 / 序号 */
.fs-check {
  flex: none;
  width: 26px;
  height: 26px;
  border-radius: 8px;
  border: 1.5px solid rgba(128, 128, 128, 0.38);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  color: var(--muted);
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}
.fs-check.on {
  background: var(--accent-2);
  border-color: var(--accent-2);
  color: #fff;
}

.fs-index {
  flex: none;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: var(--accent-2);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

/* 拖拽手柄：点阵抓手 */
.fs-grip,
.fs-grip-demo {
  flex: none;
  width: 14px;
  height: 26px;
  color: var(--muted);
  opacity: 0.6;
  background-image: radial-gradient(currentColor 1.4px, transparent 1.5px);
  background-size: 7px 6.5px;
  background-position: 0 3px;
}
.fs-grip {
  cursor: grab;
}
.fs-grip:active {
  cursor: grabbing;
}
.fs-card:hover .fs-grip {
  opacity: 1;
}
.fs-grip-demo {
  height: 20px;
  opacity: 0.8;
}

.fs-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.fs-name {
  font-size: 14px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fs-sub {
  font-size: 11.5px;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 操作区：加大点按面积，删除键与上下移分隔，避免误触 */
.fs-ops {
  flex: none;
  display: flex;
  align-items: center;
  gap: 4px;
}
.fs-ops .op {
  width: 30px;
  height: 30px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--muted);
  font-size: 15px;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.fs-ops .op:hover:not(:disabled) {
  background: rgba(128, 128, 128, 0.16);
  color: inherit;
}
.fs-ops .op:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}
.fs-ops .op.danger:hover:not(:disabled) {
  background: rgba(224, 92, 92, 0.14);
  color: #e05c5c;
}
.ops-sep {
  width: 1px;
  height: 18px;
  background: rgba(128, 128, 128, 0.28);
  margin: 0 3px;
}

.fs-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1.5px dashed rgba(128, 128, 128, 0.3);
  border-radius: 10px;
  padding: 20px 12px;
  text-align: center;
  color: var(--muted);
  font-size: 13px;
}
.fs-hint {
  font-size: 12px;
}

@media (max-width: 820px) {
  .fs-cols {
    grid-template-columns: 1fr;
  }
}
</style>
