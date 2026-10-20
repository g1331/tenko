import { reactive, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { parseQuery, sameState, serializeQuery, type SwitchState } from './model'

/**
 * 视图 / 选中项 / 搜索 / 筛选状态，镜像在路由 query 中（router.replace，不堆叠历史）：
 * 刷新、轮询重绘和分享链接都以它为准。输入框读写本地 state，避免路由异步更新时
 * 回写旧值吃掉连续输入的字符；路由被外部改变（侧栏再次点击、手改地址）时再采纳。
 */
export function useSwitchState() {
  const route = useRoute()
  const router = useRouter()
  const state = reactive<SwitchState>(parseQuery(route.query))
  let pending = 0

  watch(state, async () => {
    if (route.name !== 'switches') return
    pending += 1
    try {
      await router.replace({ name: 'switches', query: serializeQuery(state) })
    } finally {
      pending -= 1
    }
  })

  watch(
    () => route.query,
    (query) => {
      if (pending || route.name !== 'switches') return
      const next = parseQuery(query)
      if (!sameState(next, state)) Object.assign(state, next)
    },
  )

  return {
    state,
    setView: (view: SwitchState['view']) =>
      Object.assign(state, { view, id: '', q: '', gq: '', f: 'all' }),
    pick: (id: string) => Object.assign(state, { id, gq: '', f: 'all' }),
    /** 跳到“按插件查看”的指定插件（全局功能区的链接）。 */
    openPlugin: (id: string) =>
      Object.assign(state, { view: 'plugin', id, q: '', gq: '', f: 'all' }),
  }
}
