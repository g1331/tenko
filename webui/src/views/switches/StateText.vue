<script setup lang="ts">
// 开关旁的状态文字（已开启 / 已关闭），固定最小宽度避免开关左右跳动。
defineProps<{ on: boolean; onText?: string; offText?: string }>()
</script>

<template>
  <span class="sw-state" :class="{ on }">{{
    on ? (onText ?? '已开启') : (offText ?? '已关闭')
  }}</span>
</template>

<style scoped>
.sw-state {
  min-width: 3.2em;
  color: var(--tk-muted);
  font-size: 12px;
  font-weight: 600;
  text-align: right;
}
.sw-state.on {
  color: var(--tk-ok);
}
</style>
