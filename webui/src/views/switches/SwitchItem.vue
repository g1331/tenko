<script setup lang="ts">
// 详情里的一行：名称（+ 状态标签）、标识 / 说明、右侧控件；dim 表示该功能当前不生效。
defineProps<{ name: string; code?: string; note?: string; dim?: boolean }>()
</script>

<template>
  <li class="sw-item" :class="{ 'is-dim': dim }">
    <div class="sw-item-main">
      <span class="sw-item-name">{{ name }}<slot name="pills" /></span>
      <span v-if="code || note" class="sw-item-sub">
        <code v-if="code">{{ code }}</code>
        <span v-if="note">{{ note }}</span>
      </span>
    </div>
    <div class="sw-control"><slot /></div>
  </li>
</template>

<style scoped>
.sw-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px 14px;
  padding: 11px 20px;
  border-top: 1px dashed var(--tk-line-strong);
}
.sw-item.is-dim .sw-item-name {
  color: var(--tk-muted);
}
.sw-item-main {
  display: grid;
  min-width: 0;
  gap: 3px;
}
.sw-item-name {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
  color: var(--tk-heading);
  font-size: 14px;
  font-weight: 700;
  overflow-wrap: anywhere;
}
.sw-item-sub {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px 10px;
  color: var(--tk-muted);
  font-size: 12px;
}
.sw-item-sub code {
  font-size: 11px;
}
.sw-control {
  display: flex;
  flex: none;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 700px) {
  .sw-item {
    padding-inline: 14px;
  }
}
</style>
