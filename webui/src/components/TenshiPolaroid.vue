<script setup lang="ts">
import tenshi from '@/assets/tenshi.webp'

defineProps<{ size?: 'sm' | 'lg' }>()
</script>

<template>
  <div class="polaroid" :class="size === 'lg' ? 'is-lg' : ''">
    <img class="polaroid-img" :src="tenshi" alt="" />
    <div class="polaroid-cap">比那名居天子 · HINANAWI TENSHI</div>
  </div>
</template>

<style scoped>
.polaroid {
  --w: 140px;
  --h: 98px;
  width: var(--w);
  padding: 6px 6px 16px;
  background: #fff;
  box-shadow:
    -6px 6px 0 rgba(16, 45, 71, 0.85),
    0 10px 22px rgba(0, 0, 0, 0.3);
  transform: rotate(-2.5deg);
}
.polaroid.is-lg {
  --w: 168px;
  --h: 120px;
}
.polaroid-img {
  display: block;
  width: 100%;
  height: var(--h);
  object-fit: cover;
  object-position: 50% 26%;
}
.polaroid-cap {
  margin-top: 6px;
  color: #7d8793;
  font-size: 8px;
  letter-spacing: 1.6px;
  text-align: center;
}
</style>
