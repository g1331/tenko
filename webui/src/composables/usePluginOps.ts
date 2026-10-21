import { computed, inject, onScopeDispose, provide, ref, shallowRef, type InjectionKey } from 'vue'
import {
  api,
  ApiError,
  type InstalledData,
  type MarketData,
  type PluginTask,
  type PluginTaskBody,
} from '@/api'
import { resource, usePanelData, type Resource } from '@/composables/usePanelData'
import { useRestart } from '@/composables/useRestart'

const TASK_POLL_MS = 1000
const MAX_POLL_FAILURES = 5

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
const messageOf = (error: unknown) => (error as Error).message

/**
 * 插件页的共享状态：市场目录、已安装插件、后台任务与重启流程。
 * 由 PluginsView 创建并 provide，子组件通过 usePluginOps() 取用。
 */
export function createPluginOps() {
  const panel = usePanelData()
  const market = resource<MarketData>()
  const installed = resource<InstalledData>()
  const task = shallowRef<PluginTask | null>(null)
  const starting = ref(false)
  /** 本页已有任务成功：卸载等变更不会体现在 restart_required 上，需要自己记着。 */
  const changed = ref(false)
  const { restarting, failed: restartFailed, restart: requestRestart } = useRestart()
  let alive = true
  onScopeDispose(() => {
    alive = false
  })

  const busy = computed(() => starting.value || task.value?.state === 'running' || restarting.value)
  const restartNeeded = computed(() => changed.value || !!installed.data.value?.restart_required)

  async function load<T>(target: Resource<T>, read: () => Promise<T>) {
    try {
      target.data.value = await read()
      target.error.value = null
    } catch (error) {
      target.error.value = error instanceof ApiError ? error : new ApiError(messageOf(error))
    }
    target.loading.value = false
  }

  /** 重新读取已安装插件；发现后台仍有任务在跑时接着轮询。 */
  async function loadInstalled() {
    await load(installed, api.installedPlugins)
    const running = installed.data.value?.task
    if (running && task.value?.id !== running.id) void follow(running)
  }
  const loadMarket = (refresh = false) => load(market, () => api.market(refresh))
  let marketLoaded = false
  const ensureMarket = () => {
    if (marketLoaded) return
    marketLoaded = true
    return loadMarket()
  }

  async function follow(first: PluginTask) {
    task.value = first
    let failures = 0
    while (alive && task.value?.state === 'running') {
      await sleep(TASK_POLL_MS)
      try {
        task.value = await api.manage.pluginTaskState(first.id)
        failures = 0
      } catch (error) {
        // 任务记录只在进程内，重启后会变成 404；连续失败也不再空等。
        if (
          (error instanceof ApiError && error.status === 404) ||
          ++failures >= MAX_POLL_FAILURES
        ) {
          task.value = null
          ElMessage.error(`无法获取任务状态：${messageOf(error)}`)
          break
        }
      }
    }
    if (!alive) return
    if (task.value?.state === 'succeeded') changed.value = true
    await settle()
  }

  /** 任务结束后同步已安装列表、市场和共享的插件数据。 */
  async function settle() {
    await Promise.all([loadInstalled(), marketLoaded ? loadMarket() : undefined, panel.refresh()])
  }

  /** 提交任务；失败（含 409 已有任务）以 toast 提示。 */
  async function run(body: Omit<PluginTaskBody, 'confirm'>) {
    starting.value = true
    try {
      void follow(await api.manage.pluginTask({ ...body, confirm: true }))
    } catch (error) {
      ElMessage.error(messageOf(error))
    } finally {
      starting.value = false
    }
  }

  const dismissTask = () => {
    if (task.value?.state !== 'running') task.value = null
  }

  /** 重启并等面板恢复应答后，重置本页状态并取回数据。 */
  async function restart() {
    if (!(await requestRestart())) return
    changed.value = false
    task.value = null
    void load(installed, api.installedPlugins)
    if (marketLoaded) void loadMarket()
    void panel.refresh()
  }

  return {
    market,
    installed,
    task,
    busy,
    restarting,
    restartFailed,
    restartNeeded,
    markChanged: () => (changed.value = true),
    loadInstalled,
    loadMarket,
    ensureMarket,
    run,
    dismissTask,
    restart,
  }
}

export type PluginOps = ReturnType<typeof createPluginOps>
const KEY: InjectionKey<PluginOps> = Symbol('plugin-ops')

export function providePluginOps(): PluginOps {
  const ops = createPluginOps()
  provide(KEY, ops)
  return ops
}

export function usePluginOps(): PluginOps {
  const ops = inject(KEY)
  if (!ops) throw new Error('usePluginOps 必须在 PluginsView 内使用')
  return ops
}
