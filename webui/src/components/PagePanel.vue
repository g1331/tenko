<script setup lang="ts">
// 页面内的卡片：顶部 3px 色条（navy / coral / gold）+ 可选标题、右上角操作和 kicker。
defineProps<{ title?: string; accent?: 'navy' | 'coral' | 'gold' }>()
</script>

<template>
  <el-card
    class="page-panel"
    :class="`is-${accent ?? 'navy'}`"
    shadow="never"
    :body-class="'page-panel-body'"
  >
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
  --el-card-border-color: transparent;
  --el-card-padding: 18px 20px;
  border: 0;
  border-top: 3px solid var(--tk-rule);
  border-radius: 4px;
  box-shadow: var(--tk-shadow);
}
.page-panel.is-coral {
  border-top-color: var(--tk-coral);
}
.page-panel.is-gold {
  border-top-color: var(--tk-gold);
}
.page-panel :deep(.el-card__header) {
  padding: 18px 20px 0;
  border-bottom: 0;
}
.panel-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.panel-head h3 {
  margin: 0;
  color: var(--tk-heading);
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 2px;
}
.panel-extra {
  color: var(--tk-muted);
  font-size: 12px;
}
.page-panel :deep(.el-card__header + .el-card__body) {
  padding-top: 12px;
}
</style>
