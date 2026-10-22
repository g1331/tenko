<script setup lang="ts">
import type { ChatSegment } from '@/api'

// 一条消息的内容：文字、@、表情、图片和引用按段渲染，其他元素显示占位。
// 点引用抛出 jump，由消息流定位原消息。
defineProps<{ content: ChatSegment[] }>()
const emit = defineEmits<{ jump: [messageId: string] }>()
</script>

<template>
  <div class="chat-content">
    <template v-for="(segment, index) in content" :key="index">
      <button
        v-if="segment.type === 'quote'"
        type="button"
        class="quote"
        :disabled="!segment.id"
        title="跳到原消息"
        @click="emit('jump', segment.id)"
      >
        <span class="quote-name">{{ segment.name || segment.user_id || '回复' }}</span>
        <span class="quote-text">{{ segment.text || '一条消息' }}</span>
      </button>
      <span v-else-if="segment.type === 'text'" class="text">{{ segment.text }}</span>
      <span v-else-if="segment.type === 'at'" class="at">@{{ segment.name || segment.id }} </span>
      <span v-else-if="segment.type === 'face'" class="chip" :title="`QQ 表情 ${segment.id}`">
        [{{ segment.name || '表情' }}]
      </span>
      <el-image
        v-else-if="segment.type === 'image' && segment.src"
        class="image"
        :src="segment.src"
        :preview-src-list="[segment.src]"
        fit="contain"
        preview-teleported
        hide-on-click-modal
      >
        <template #error><span class="chip">[图片已过期]</span></template>
      </el-image>
      <span v-else-if="segment.type === 'image'" class="chip">[图片]</span>
      <span v-else class="chip">[{{ segment.name }}]</span>
    </template>
  </div>
</template>

<style scoped>
.chat-content {
  min-width: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.6;
}
.quote {
  display: grid;
  gap: 1px;
  width: 100%;
  min-width: 120px;
  margin: 2px 0 6px;
  padding: 5px 9px;
  border: 0;
  border-left: 3px solid var(--tk-line-strong);
  border-radius: 6px;
  background: color-mix(in srgb, var(--tk-text) 6%, transparent);
  color: var(--tk-muted);
  font: inherit;
  font-size: 12px;
  line-height: 1.5;
  text-align: left;
  white-space: normal;
  cursor: pointer;
}
.quote:disabled {
  cursor: default;
}
.quote-name {
  font-weight: 600;
}
.quote-text {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.at {
  color: var(--tk-primary);
}
.chip {
  color: var(--tk-muted);
}
.image {
  display: block;
  max-width: min(300px, 100%);
  max-height: 300px;
  margin: 4px 0;
  border-radius: 8px;
  cursor: zoom-in;
}
.image:only-child {
  margin: 0;
}
.image :deep(img) {
  max-height: 300px;
}
</style>
