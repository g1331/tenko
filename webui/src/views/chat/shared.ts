import { onBeforeUnmount, ref } from 'vue'
import type { ChatSegment } from '@/api'

/** 回复条和引用里显示的一行摘要。 */
export function previewOf(content: ChatSegment[]): string {
  return content
    .map((item) =>
      item.type === 'text'
        ? item.text
        : item.type === 'at'
          ? `@${item.name || item.id}`
          : item.type === 'image'
            ? '[图片]'
            : item.type === 'face'
              ? '[表情]'
              : item.type === 'other'
                ? `[${item.name}]`
                : '',
    )
    .join('')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 40)
}

/**
 * 聊天区铺满窗口的沉浸模式，Esc 退出。
 *
 * 用 fixed 铺满而不是 Fullscreen API：图片预览和 @ 菜单挂在 body 上，
 * 浏览器全屏时只渲染全屏元素内部，它们会看不见。
 */
export function useImmersive() {
  const immersive = ref(false)
  function onKeydown(event: KeyboardEvent) {
    if (event.key !== 'Escape' || !immersive.value || event.defaultPrevented) return
    // Esc 先交给打开的图片预览。
    if (document.querySelector('.el-image-viewer__wrapper')) return
    immersive.value = false
  }
  // 捕获阶段：图片预览在 document 上处理 Esc 并立即卸载，冒泡到 window 时已经查不到它。
  window.addEventListener('keydown', onKeydown, true)
  onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown, true))
  return immersive
}
