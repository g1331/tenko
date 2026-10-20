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
        <el-icon :size="16"><component :is="item.icon" /></el-icon>
      </el-radio-button>
    </el-tooltip>
  </el-radio-group>
</template>

<style scoped>
.theme-switch {
  flex-wrap: nowrap;
  padding: 3px;
  gap: 2px;
  border: 1px solid var(--tswitch-border);
  border-radius: 8px;
}
.is-navy {
  --tswitch-border: #536c80;
  --tswitch-fg: #becdd9;
  --tswitch-hover: #304f67;
  --tswitch-on-bg: #f5f1e8;
  --tswitch-on-fg: #1e3a52;
}
.is-page {
  --tswitch-border: var(--tk-line-strong);
  --tswitch-fg: var(--tk-muted);
  --tswitch-hover: var(--tk-off-bg);
  --tswitch-on-bg: var(--tk-navy-solid);
  --tswitch-on-fg: var(--tk-on-navy);
}
.theme-switch :deep(.el-radio-button__inner) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border: 0;
  border-radius: 5px;
  background: transparent;
  box-shadow: none;
  color: var(--tswitch-fg);
}
.theme-switch :deep(.el-radio-button__inner:hover) {
  background: var(--tswitch-hover);
  color: var(--tswitch-on-bg);
}
.theme-switch :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
  background: var(--tswitch-on-bg);
  color: var(--tswitch-on-fg);
  box-shadow: none;
}
</style>
