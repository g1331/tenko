<script setup lang="ts">
// 所有页面的根容器：统一入场动画，并提供页面级工具栏（#actions，位于内容上方）。
</script>

<template>
  <div class="page-container">
    <div v-if="$slots.actions" class="page-actions"><slot name="actions" /></div>
    <slot />
  </div>
</template>

<style scoped>
.page-container {
  min-width: 0;
  animation: tk-page-in 260ms ease backwards;
}
.page-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  margin-bottom: 16px;
}
</style>
