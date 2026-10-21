<script setup lang="ts" generic="T">
import { ref } from 'vue'
import { Search } from '@element-plus/icons-vue'
import PagePanel from '@/components/PagePanel.vue'

/** 左侧可搜索列表：↓ 从搜索框进入列表，↑↓ / Home / End 移动焦点，Enter 选中。 */
const props = defineProps<{
  items: T[]
  itemKey: (item: T) => string
  selected: string
  label: string
  placeholder: string
  count: string
  empty: string
}>()
const query = defineModel<string>({ required: true })
const emit = defineEmits<{ pick: [key: string] }>()
defineSlots<{ row(props: { item: T }): unknown }>()

const list = ref<HTMLElement>()

function rows(): HTMLButtonElement[] {
  return [...(list.value?.querySelectorAll<HTMLButtonElement>('.sw-row') ?? [])]
}
function onSearchKey(event: Event) {
  const key = event as KeyboardEvent
  if (key.key === 'ArrowDown') {
    const all = rows()
    ;(all.find((row) => row.getAttribute('aria-current') === 'true') ?? all[0])?.focus()
    key.preventDefault()
  } else if (key.key === 'Enter' && !key.isComposing) {
    const [firstItem] = props.items
    if (firstItem) emit('pick', props.itemKey(firstItem))
    key.preventDefault()
  }
}
function onRowKey(event: KeyboardEvent) {
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
  const all = rows()
  const index = all.indexOf(event.target as HTMLButtonElement)
  const next =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? all.length - 1
        : index + (event.key === 'ArrowDown' ? 1 : -1)
  if (next < 0) document.querySelector<HTMLInputElement>('.sw-search input')?.focus()
  else all[Math.min(next, all.length - 1)]?.focus()
  event.preventDefault()
}
</script>

<template>
  <PagePanel class="sw-side">
    <el-input
      v-model="query"
      class="sw-search"
      clearable
      :aria-label="`搜索${label}`"
      :placeholder="placeholder"
      @keydown="onSearchKey"
    >
      <template #prefix>
        <el-icon><Search /></el-icon>
      </template>
    </el-input>
    <div class="sw-meta" role="status" aria-live="polite">{{ count }}</div>
    <el-scrollbar class="sw-list" :aria-label="`${label}列表`">
      <ul ref="list" @keydown="onRowKey">
        <li v-for="item in items" :key="itemKey(item)">
          <button
            type="button"
            class="sw-row"
            :aria-current="itemKey(item) === selected ? 'true' : undefined"
            @click="emit('pick', itemKey(item))"
          >
            <slot name="row" :item="item" />
          </button>
        </li>
        <li v-if="!items.length" class="sw-empty">{{ empty }}</li>
      </ul>
    </el-scrollbar>
  </PagePanel>
</template>

<style scoped>
.sw-side.sw-side {
  --el-card-padding: 0;
  position: sticky;
  top: 12px;
}
.sw-search {
  padding: 12px 12px 0;
}
.sw-meta {
  padding: 8px 14px;
  border-bottom: 1px solid var(--tk-line);
  color: var(--tk-muted);
  font-size: 12px;
}
.sw-list {
  height: auto;
}
.sw-list :deep(.el-scrollbar__wrap) {
  max-height: calc(100dvh - 280px);
  min-height: 80px;
}
.sw-list ul {
  margin: 0;
  padding: 0;
  list-style: none;
}
.sw-row {
  display: flex;
  width: 100%;
  min-height: 54px;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 14px 8px 11px;
  border: 0;
  border-bottom: 1px solid var(--tk-line);
  border-left: 3px solid transparent;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font: inherit;
  text-align: left;
}
.sw-list li:last-child .sw-row {
  border-bottom: 0;
}
.sw-row:hover {
  background: var(--tk-surface-2);
}
.sw-row[aria-current='true'] {
  border-left-color: var(--tk-primary);
  background: var(--tk-primary-soft);
}
.sw-row:focus-visible {
  outline-offset: -3px;
}
.sw-empty {
  padding: 24px 14px;
  color: var(--tk-muted);
  text-align: center;
}

@media (max-width: 700px) {
  .sw-side {
    position: static;
  }
  .sw-list :deep(.el-scrollbar__wrap) {
    max-height: 230px;
  }
}
</style>
