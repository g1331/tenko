import { computed, onScopeDispose, readonly, ref, shallowRef, type Ref } from 'vue'
import {
  api,
  ApiError,
  type AccountsData,
  type FeaturesData,
  type ManagedAccountsData,
  type OverviewData,
  type SettingsData,
} from '@/api'
import { retryLive, startLive, stopLive, useLiveState } from './useLiveEvents'

export const POLL_INTERVAL_MS = 30_000
/** 实时连接正常时，兜底的全量刷新间隔是 SAFETY_TICKS 个轮询周期（120 秒）。 */
const SAFETY_TICKS = 4

/** 单个数据源：data 在刷新失败时保留上一次成功的值，error 记录最近一次失败。 */
export interface Resource<T> {
  data: Ref<T | null>
  error: Ref<ApiError | null>
  /** 首次加载中（尚无数据）；后台刷新不会置位，避免界面闪回骨架屏。 */
  loading: Ref<boolean>
}

export function resource<T>(): Resource<T> {
  return { data: shallowRef<T | null>(null), error: shallowRef(null), loading: ref(true) }
}

const overview = resource<OverviewData>()
const accounts = resource<AccountsData>()
const features = resource<FeaturesData>()
const managedAccounts = resource<ManagedAccountsData>()
const settings = resource<SettingsData>()

const live = useLiveState()
const refreshing = ref(false)
const lastSynced = shallowRef<Date | null>(null)
/** 每次重置递增，使重置前发出的请求结果作废（登出后不能写回数据）。 */
let epoch = 0
let inFlight: Promise<void> | null = null
/** 等在途请求结束后再跑的那一轮；期间的多次 refresh() 共用它。 */
let queued: Promise<void> | null = null

function source<T>(target: Resource<T>, load: () => Promise<T>) {
  /** 推送计数：请求发出之后又收到推送，说明该响应比推送旧，不能覆盖它。 */
  let pushes = 0
  return {
    target,
    push(value: T): void {
      pushes += 1
      target.data.value = value
      target.error.value = null
      target.loading.value = false
    },
    async run(current: number): Promise<void> {
      const seen = pushes
      try {
        const value = await load()
        if (current !== epoch) return
        if (pushes === seen) {
          target.data.value = value
          target.error.value = null
        }
      } catch (reason) {
        if (current !== epoch) return
        target.error.value =
          reason instanceof ApiError ? reason : new ApiError(String((reason as Error)?.message))
      }
      target.loading.value = false
    },
  }
}

const overviewSource = source(overview, api.overview)
const sources = [
  overviewSource,
  source(accounts, api.accounts),
  source(features, api.features),
  source(managedAccounts, api.manage.accounts),
  source(settings, api.manage.settings),
]

function start(): Promise<void> {
  const current = epoch
  refreshing.value = true
  const round = Promise.all(sources.map((item) => item.run(current))).then(() => {
    if (current !== epoch) return
    lastSynced.value = new Date()
    inFlight = null
    if (!queued) refreshing.value = false
  })
  inFlight = round
  return round
}

/**
 * 重新读取全部数据源。已有请求在途时，它可能发出得比刚完成的写操作更早、返回旧数据，
 * 所以不能复用它，而是在它结束后再跑一轮；等待期间的多次调用合并为同一轮。
 * 返回的 Promise 在“调用之后开始的那一轮”完成时结束。
 */
function refresh(): Promise<void> {
  if (!inFlight) return start()
  if (!queued) {
    const current = epoch
    queued = inFlight.then(() => {
      if (current !== epoch) return // 期间已重置（登出），不再发请求
      queued = null
      return start()
    })
  }
  return queued
}

/** 聊天记录有新消息时递增；聊天页监听它增量拉取，不触发整体刷新。 */
const chatSignal = ref(0)

/** 只重读指定数据源（实时通知“某类数据变了”时用）；没有匹配的名字就整体刷新。 */
function refreshResources(names: string[]): Promise<void> {
  if (names.includes('chat')) {
    chatSignal.value += 1
    names = names.filter((name) => name !== 'chat')
    if (!names.length) return Promise.resolve()
  }
  const wanted = new Set<Resource<unknown>>()
  if (names.includes('accounts'))
    [accounts, managedAccounts, overview].forEach((item) => wanted.add(item))
  if (names.includes('features')) wanted.add(features)
  if (!wanted.size) return refresh()
  const current = epoch
  return Promise.all(
    sources.filter((item) => wanted.has(item.target)).map((item) => item.run(current)),
  ).then(() => undefined)
}

/** 清空全部数据并使在途请求作废（登出 / 会话失效时调用）。 */
function reset(): void {
  epoch += 1
  inFlight = null
  queued = null
  refreshing.value = false
  lastSynced.value = null
  for (const { target } of sources) {
    target.data.value = null
    target.error.value = null
    target.loading.value = true
  }
}

export type SyncState = 'syncing' | 'ok' | 'partial' | 'offline'

/** 同步概况：state 用于状态点和文案，ready / total 表示有数据的数据源个数。 */
const sync = computed(() => {
  const all = sources.map((item) => item.target)
  const failed = all.filter((target) => target.error.value).length
  const ready = all.filter((target) => target.data.value).length
  let state: SyncState = failed ? 'partial' : 'ok'
  if (!lastSynced.value) state = 'syncing'
  else if (failed && !ready) state = 'offline'
  return { state, ready, total: all.length }
})

/**
 * 在布局组件中调用：挂载时立即加载并建立实时连接，卸载时关闭。
 * 实时连接正常时 REST 刷新只是兜底（每 120 秒）；连接不可用时回到每 30 秒轮询。
 * 页面直接用 usePanelData() 读取数据，不要自己再起定时器。
 */
export function usePanelPolling(): void {
  let ticks = 0
  const timer = setInterval(() => {
    if (document.hidden) return
    ticks += 1
    if (live.value !== 'live' || ticks >= SAFETY_TICKS) {
      ticks = 0
      void refresh()
    }
  }, POLL_INTERVAL_MS)
  const onVisible = () => {
    const stale = !lastSynced.value || Date.now() - lastSynced.value.getTime() >= POLL_INTERVAL_MS
    if (!document.hidden && stale) void refresh()
  }
  document.addEventListener('visibilitychange', onVisible)
  void refresh()
  startLive({
    overview: (data) => overviewSource.push(data),
    changed: (names) => void refreshResources(names),
    recovered: () => void refresh(),
  })
  onScopeDispose(() => {
    clearInterval(timer)
    document.removeEventListener('visibilitychange', onVisible)
    stopLive()
    reset()
  })
}

/** 共享的面板数据与同步状态。写操作成功后调用 refresh() 取回最新状态。 */
export function usePanelData() {
  return {
    overview,
    accounts,
    features,
    managedAccounts,
    settings,
    refreshing: readonly(refreshing),
    chatSignal: readonly(chatSignal),
    lastSynced: readonly(lastSynced),
    sync,
    live,
    /** 立即重连实时通道并整体刷新。 */
    retryLive: () => {
      retryLive()
      return refresh()
    },
    refresh,
    reset,
  }
}
