import { readonly, ref } from 'vue'
import { notifyUnauthorized, type LogRecord, type OverviewData } from '@/api'

/** 整个面板共用一条 SSE 连接：总览指标、日志和“数据变了”的通知；数据本身仍以 REST 为准。 */
export type LiveState = 'connecting' | 'live' | 'down'

export interface LogPush {
  records: LogRecord[]
  cursor: number
  reset: boolean
}
interface Handlers {
  overview: (data: OverviewData) => void
  changed: (resources: string[]) => void
  /** 连接从中断恢复：中断期间可能漏了推送，需要整体拉取一次。 */
  recovered: () => void
}
interface LogSubscriber {
  cursor: () => number
  onLogs: (data: LogPush) => void
}

const RETRY_MIN_MS = 3000
const RETRY_MAX_MS = 30_000
/** 短暂抖动由浏览器自动重连吸收，超过这段时间仍没恢复才提示中断。 */
const DOWN_GRACE_MS = 5000

const state = ref<LiveState>('connecting')
let handlers: Handlers | null = null
let logs: LogSubscriber | null = null
let source: EventSource | null = null
let retryTimer: ReturnType<typeof setTimeout> | undefined
let downTimer: ReturnType<typeof setTimeout> | undefined
let backoff = RETRY_MIN_MS
let wasDown = false

function parse<T>(event: Event): T | null {
  try {
    return JSON.parse((event as MessageEvent<string>).data) as T
  } catch {
    return null
  }
}

function close(): void {
  clearTimeout(retryTimer)
  source?.close()
  source = null
}

function open(): void {
  close()
  const topics = logs ? 'overview,logs' : 'overview'
  // 新建连接没有 Last-Event-ID，日志从订阅方已有的游标续上。
  const after = logs ? `&after=${logs.cursor()}` : ''
  const stream = new EventSource(`/webui/api/events?topics=${topics}${after}`)
  source = stream
  stream.onopen = () => {
    clearTimeout(downTimer)
    downTimer = undefined
    backoff = RETRY_MIN_MS
    state.value = 'live'
    if (wasDown) {
      wasDown = false
      handlers?.recovered()
    }
  }
  stream.onerror = () => {
    wasDown = true
    downTimer ??= setTimeout(() => (state.value = 'down'), DOWN_GRACE_MS)
    // 浏览器会自动重连网络错误；被服务端拒绝（429 / 502 等）时连接已关闭，需要自己退避重连。
    if (stream.readyState !== EventSource.CLOSED) return
    close()
    retryTimer = setTimeout(open, backoff)
    backoff = Math.min(backoff * 2, RETRY_MAX_MS)
  }
  stream.addEventListener('overview', (event) => {
    const data = parse<OverviewData>(event)
    if (data) handlers?.overview(data)
  })
  stream.addEventListener('logs', (event) => {
    const data = parse<LogPush>(event)
    if (data) logs?.onLogs(data)
  })
  stream.addEventListener('changed', (event) => {
    handlers?.changed(parse<{ resources: string[] }>(event)?.resources ?? [])
  })
  stream.addEventListener('unauthorized', () => {
    // 会话已被注销或过期：等同 401，不再重连。
    close()
    state.value = 'down'
    notifyUnauthorized()
  })
}

/** 在布局中启动 / 在卸载时停止。 */
export function startLive(next: Handlers): void {
  handlers = next
  open()
}
export function stopLive(): void {
  close()
  clearTimeout(downTimer)
  downTimer = undefined
  handlers = null
  state.value = 'connecting'
  wasDown = false
  backoff = RETRY_MIN_MS
}

/** 立即重连（不等退避）。 */
export function retryLive(): void {
  backoff = RETRY_MIN_MS
  if (handlers) open()
}

/** 日志页订阅 logs 主题；返回取消订阅函数。订阅与取消会让连接按新的主题重建。 */
export function subscribeLogs(subscriber: LogSubscriber): () => void {
  logs = subscriber
  if (handlers) open()
  return () => {
    if (logs !== subscriber) return
    logs = null
    if (handlers) open()
  }
}

export function useLiveState() {
  return readonly(state)
}
