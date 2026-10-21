<script setup lang="ts">
import { computed, ref, watch } from 'vue'

// 插件图标：单色 SVG（glyph）用遮罩按主题着色；目录 PNG 原样显示；没有图标或加载失败时
// 用名称首字做字母头像。
const props = withDefaults(
  defineProps<{
    name: string
    src: string | null
    glyph?: boolean
    size?: number
    /** solid：深色底白色图形，用在详情页页首。 */
    solid?: boolean
  }>(),
  { glyph: false, size: 40, solid: false },
)
const failed = ref(false)
watch(
  () => props.src,
  () => (failed.value = false),
)
const letter = computed(() => Array.from(props.name.trim())[0]?.toUpperCase() ?? '?')
const box = computed(() => ({
  '--icon-size': `${props.size}px`,
  '--glyph': props.src ? `url("${props.src}")` : undefined,
}))
</script>

<template>
  <span
    v-if="src && glyph"
    class="plugin-icon glyph"
    :class="{ solid }"
    :style="box"
    aria-hidden="true"
  />
  <img
    v-else-if="src && !failed"
    class="plugin-icon"
    :class="{ solid }"
    :style="box"
    :src="src"
    alt=""
    @error="failed = true"
  />
  <span v-else class="plugin-icon letter" :class="{ solid }" :style="box" aria-hidden="true">{{
    letter
  }}</span>
</template>

<style scoped>
.plugin-icon {
  position: relative;
  display: inline-flex;
  width: var(--icon-size);
  height: var(--icon-size);
  flex: none;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--tk-line);
  border-radius: calc(var(--icon-size) * 0.24);
  background: var(--tk-surface-2);
  object-fit: cover;
}
.glyph {
  border-color: transparent;
  background: var(--tk-primary-soft);
}
.glyph::after {
  width: 56%;
  height: 56%;
  background: var(--tk-primary);
  content: '';
  mask: var(--glyph) center / contain no-repeat;
}
.letter {
  color: var(--tk-heading);
  font-size: calc(var(--icon-size) * 0.45);
  font-weight: 700;
}
.solid {
  border-color: transparent;
  background: var(--tk-sidebar-bg);
  box-shadow: inset 0 0 0 1px rgb(255 255 255 / 6%);
}
.solid.glyph::after {
  background: #e8eeff;
}
.solid.letter {
  color: #e8eeff;
}
/* 深色主题下侧栏色比卡片还深，改用强调色的浅底。 */
html.dark .plugin-icon.solid {
  background: var(--tk-primary-soft);
}
html.dark .plugin-icon.solid.glyph::after {
  background: var(--tk-primary);
}
html.dark .plugin-icon.solid.letter {
  color: var(--tk-primary);
}
</style>
