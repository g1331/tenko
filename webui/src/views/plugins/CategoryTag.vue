<script lang="ts">
// 分类按目录给出的顺序取色，同一分类在筛选条、卡片和详情里颜色一致。
const PALETTE = [
  '#3b82f6',
  '#8b5cf6',
  '#ec4899',
  '#f59e0b',
  '#10b981',
  '#06b6d4',
  '#ef4444',
  '#6366f1',
  '#64748b',
]
export function categoryColor(name: string, order: readonly string[]): string {
  return PALETTE[Math.max(order.indexOf(name), 0) % PALETTE.length]!
}
</script>

<script setup lang="ts">
import { computed } from 'vue'
import { usePluginOps } from '@/composables/usePluginOps'

const props = defineProps<{ name: string }>()
const ops = usePluginOps()
const color = computed(() => categoryColor(props.name, ops.market.data.value?.categories ?? []))
</script>

<template>
  <span class="category" :style="{ '--c': color }">{{ name }}</span>
</template>

<style scoped>
.category {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 1px 8px 1px 7px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--c) 12%, transparent);
  /* 向标题色偏一点：浅色主题下更深、深色主题下更亮，黄色、青色也读得清 */
  color: color-mix(in srgb, var(--c) 72%, var(--tk-heading));
  font-size: 12px;
  font-weight: 600;
  line-height: 18px;
  white-space: nowrap;
}
.category::before {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--c);
  content: '';
}
</style>
