<script setup lang="ts">
import { computed } from 'vue'
import type { StatusKind } from '@/composables/status'

// 状态语义 → Element Plus 标签类型；页面统一用它展示在线 / 停用 / 维护等状态。
const props = defineProps<{ kind: StatusKind; size?: 'small' | 'default' }>()

const type = computed(
  () =>
    ({ on: 'success', off: 'info', pending: 'warning', danger: 'danger', prot: 'primary' })[
      props.kind
    ] as 'success' | 'info' | 'warning' | 'danger' | 'primary',
)
</script>

<template>
  <el-tag
    :type="type"
    :size="size ?? 'small'"
    effect="light"
    disable-transitions
    class="status-tag"
  >
    <slot />
  </el-tag>
</template>

<style scoped>
.status-tag {
  font-weight: 600;
  letter-spacing: 1px;
}
</style>
