<script setup lang="ts">
// 页面内的卡片：白色表面 + 发丝边框，可选标题和右上角操作。
defineProps<{ title?: string }>()
</script>

<template>
  <el-card class="page-panel" shadow="never" :body-class="'page-panel-body'">
    <template v-if="title || $slots.extra || $slots.header" #header>
      <slot name="header">
        <div class="panel-head">
          <h3>{{ title }}</h3>
          <div v-if="$slots.extra" class="panel-extra"><slot name="extra" /></div>
        </div>
      </slot>
    </template>
    <slot />
  </el-card>
</template>

<style scoped>
.page-panel {
  --el-card-border-color: var(--tk-line);
  --el-card-padding: 18px 20px;
  border-radius: var(--tk-radius);
  box-shadow: var(--tk-shadow);
}
.page-panel :deep(.el-card__header) {
  padding: 16px 20px 0;
  border-bottom: 0;
}
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.panel-head h3 {
  margin: 0;
  color: var(--tk-heading);
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.04em;
}
.panel-extra {
  color: var(--tk-muted);
  font-size: 12px;
}
.page-panel :deep(.el-card__header + .el-card__body) {
  padding-top: 12px;
}
</style>
