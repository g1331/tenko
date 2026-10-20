<script setup lang="ts">
// 品牌标记：三层堆叠 + 珊瑚色圆点，总是画在深色（海军蓝）底上。
defineProps<{ caption?: boolean }>()
</script>

<template>
  <div class="brand">
    <svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true">
      <path d="M6 8 L16 14 L26 8 L26 11 L16 17 L6 11 Z" fill="#F5F1E8" opacity="0.9" />
      <path d="M6 13 L16 19 L26 13 L26 16 L16 22 L6 16 Z" fill="#F5F1E8" opacity="0.55" />
      <path d="M6 18 L16 24 L26 18 L26 20.5 L16 26.5 L6 20.5 Z" fill="#C7A45B" opacity="0.85" />
      <circle cx="16" cy="4.5" r="2.2" fill="#D96B5B" />
    </svg>
    <div>
      <div class="brand-name">TENKO</div>
      <div v-if="caption !== false" class="brand-caption">GROUP CONTROL / WEBUI</div>
    </div>
  </div>
</template>

<style scoped>
.brand {
  display: flex;
  align-items: center;
  gap: 11px;
}
.brand-mark {
  width: 28px;
  height: 28px;
}
.brand-name {
  color: #f5f1e8;
  font-size: 19px;
  font-weight: 900;
  letter-spacing: 5px;
  line-height: 1;
}
.brand-caption {
  margin-top: 5px;
  color: rgba(245, 241, 232, 0.6);
  font-size: 9px;
  letter-spacing: 2px;
}
</style>
