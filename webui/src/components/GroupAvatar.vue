<script setup lang="ts">
import { ref, watch } from 'vue'

// QQ 群头像，地址与 satori OneBot 适配器给出的一致；加载失败时显示群名首字。
const props = withDefaults(defineProps<{ id: string; name?: string; size?: number }>(), {
  name: '',
  size: 32,
})
const failed = ref(false)
watch(
  () => props.id,
  () => (failed.value = false),
)
</script>

<template>
  <span class="group-avatar" :style="{ '--size': `${size}px` }" aria-hidden="true">
    <img
      v-if="!failed"
      :src="`https://p.qlogo.cn/gh/${id}/${id}/100`"
      alt=""
      loading="lazy"
      referrerpolicy="no-referrer"
      @error="failed = true"
    />
    <template v-else>{{ [...(name || id)][0] }}</template>
  </span>
</template>

<style scoped>
.group-avatar {
  display: inline-grid;
  flex: none;
  width: var(--size);
  height: var(--size);
  place-items: center;
  overflow: hidden;
  border-radius: 8px;
  background: var(--tk-surface-2);
  color: var(--tk-muted);
  font-size: calc(var(--size) * 0.42);
  font-weight: 700;
}
.group-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
</style>
