<script setup lang="ts">
import { Monitor, Moon, Sunny } from '@element-plus/icons-vue'
import { useTheme, type ThemePreference } from '@/composables/useTheme'

// tone：navy 用于深蓝侧栏 / 顶栏，page 用于随页面主题着色的位置（登录页）。
defineProps<{ tone?: 'navy' | 'page' }>()
const { preference, setTheme } = useTheme()

const options: Array<{ value: ThemePreference; label: string; icon: typeof Sunny }> = [
  { value: 'light', label: '浅色主题', icon: Sunny },
  { value: 'dark', label: '深色主题', icon: Moon },
  { value: 'system', label: '跟随系统', icon: Monitor },
]
</script>

<template>
  <el-radio-group
    class="theme-switch"
    :class="tone === 'page' ? 'is-page' : 'is-navy'"
    :model-value="preference"
    size="small"
    aria-label="主题"
    @update:model-value="(value) => setTheme(value as ThemePreference)"
  >
    <el-tooltip v-for="item in options" :key="item.value" :content="item.label" placement="top">
      <el-radio-button :value="item.value" :aria-label="item.label">
        <el-icon :size="14"><component :is="item.icon" /></el-icon>
      </el-radio-button>
    </el-tooltip>
  </el-radio-group>
</template>

<style scoped>
.theme-switch {
  flex-wrap: nowrap;
  padding: 3px;
  gap: 2px;
  border-radius: 8px;
  background: var(--tswitch-bg);
}
.is-navy {
  --tswitch-bg: rgba(255, 255, 255, 0.06);
  --tswitch-fg: var(--tk-side-muted);
  --tswitch-hover: rgba(255, 255, 255, 0.08);
  --tswitch-on-bg: rgba(255, 255, 255, 0.14);
  --tswitch-on-fg: #fff;
}
.is-page {
  --tswitch-bg: var(--tk-off-bg);
  --tswitch-fg: var(--tk-muted);
  --tswitch-hover: var(--tk-line);
  --tswitch-on-bg: var(--tk-surface);
  --tswitch-on-fg: var(--tk-primary);
}
.theme-switch :deep(.el-radio-button .el-radio-button__inner) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 24px;
  padding: 0;
  border: 0;
  outline: 0;
  border-radius: 6px;
  background: transparent;
  box-shadow: none;
  color: var(--tswitch-fg);
}
.theme-switch :deep(.el-radio-button__inner:hover) {
  background: var(--tswitch-hover);
  color: var(--tswitch-on-fg);
}
.theme-switch :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
  background: var(--tswitch-on-bg);
  color: var(--tswitch-on-fg);
  box-shadow: none;
}
.theme-switch :deep(.el-radio-button:has(:focus-visible) .el-radio-button__inner) {
  outline: 2px solid var(--el-color-primary);
}
</style>
