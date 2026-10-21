<script setup lang="ts">
import { computed, ref, watch } from 'vue'

// 插件图标：目录给了图标就显示，没有（或加载失败）时用名称首字做字母头像。
const props = defineProps<{ name: string; src: string | null }>()
const failed = ref(false)
watch(
  () => props.src,
  () => (failed.value = false),
)
const letter = computed(() => Array.from(props.name.trim())[0]?.toUpperCase() ?? '?')
</script>

<template>
  <img v-if="src && !failed" class="plugin-icon" :src="src" alt="" @error="failed = true" />
  <span v-else class="plugin-icon letter" aria-hidden="true">{{ letter }}</span>
</template>

<style scoped>
.plugin-icon {
  display: inline-flex;
  width: 40px;
  height: 40px;
  flex: none;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--tk-line);
  border-radius: 8px;
  background: var(--tk-surface-2);
  object-fit: cover;
}
.letter {
  color: var(--tk-heading);
  font-size: 18px;
  font-weight: 700;
}
</style>
