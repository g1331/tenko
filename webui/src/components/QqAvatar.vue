<script setup lang="ts">
import { computed, ref, watch } from 'vue'

// QQ 群或账号头像，地址与 satori OneBot 适配器给出的一致；非 QQ 平台或加载失败时显示名称首字。
const props = withDefaults(
  defineProps<{
    id: string
    name?: string | null
    size?: number
    kind?: 'group' | 'user'
    platform?: string
  }>(),
  { name: '', size: 32, kind: 'group', platform: 'qq-client' },
)
const failed = ref(false)
watch(
  () => props.id,
  () => (failed.value = false),
)
const src = computed(() =>
  props.platform !== 'qq-client'
    ? null
    : props.kind === 'group'
      ? `https://p.qlogo.cn/gh/${props.id}/${props.id}/100`
      : `https://q1.qlogo.cn/g?b=qq&nk=${props.id}&s=100`,
)
</script>

<template>
  <span
    class="qq-avatar"
    :class="`is-${kind}`"
    :style="{ '--size': `${size}px` }"
    aria-hidden="true"
  >
    <img
      v-if="src && !failed"
      :src="src"
      alt=""
      loading="lazy"
      referrerpolicy="no-referrer"
      @error="failed = true"
    />
    <template v-else>{{ [...(name || id)][0] }}</template>
  </span>
</template>

<style scoped>
.qq-avatar {
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
.qq-avatar.is-user {
  border-radius: 50%;
}
.qq-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
</style>
