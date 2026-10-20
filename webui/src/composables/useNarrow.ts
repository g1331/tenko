import { onScopeDispose, ref } from 'vue'

/** 视口是否不宽于 maxWidth（用于在表格与卡片列表之间切换）。 */
export function useNarrow(maxWidth = 700) {
  const query = window.matchMedia(`(max-width: ${maxWidth}px)`)
  const narrow = ref(query.matches)
  const update = () => {
    narrow.value = query.matches
  }
  query.addEventListener('change', update)
  onScopeDispose(() => query.removeEventListener('change', update))
  return narrow
}
