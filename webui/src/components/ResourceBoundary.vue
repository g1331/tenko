<script setup lang="ts" generic="T">
import { usePanelData, type Resource } from '@/composables/usePanelData'

/**
 * 数据源的统一三态：首次加载中 → 骨架屏；失败且无数据 → 带重试的空状态；有数据 → 默认插槽。
 * 后台刷新失败时保留旧数据，同步状态由页头体现，不在页面内插入提示条。
 */
withDefaults(defineProps<{ resource: Resource<T>; label?: string; rows?: number }>(), {
  label: '数据',
  rows: 3,
})
defineSlots<{ default(props: { data: T }): unknown }>()

const { refresh, refreshing } = usePanelData()
</script>

<template>
  <slot v-if="resource.data.value" :data="resource.data.value" />
  <el-skeleton v-else-if="resource.loading.value" :rows="rows" animated />
  <el-empty
    v-else
    :image-size="64"
    :description="
      resource.error.value ? `${label}暂不可用：${resource.error.value.message}` : `暂无${label}`
    "
  >
    <el-button v-if="resource.error.value" size="small" :loading="refreshing" @click="refresh">
      重试
    </el-button>
  </el-empty>
</template>
