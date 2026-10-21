import type { ApiEnvelope } from './types'

const TIMEOUT_MS = 10_000

/** 接口返回的错误：携带后端 error.code、HTTP 状态码和限流的 retry_after。 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly retryAfter: number | null

  constructor(
    message: string,
    options: { status?: number; code?: string; retryAfter?: number | null } = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = options.status ?? 0
    this.code = options.code ?? ''
    this.retryAfter = options.retryAfter ?? null
  }
}

type UnauthorizedHandler = () => void
let onUnauthorized: UnauthorizedHandler = () => {}

/** 注册会话失效处理（useAuth 在启动时注册，避免 client 反向依赖认证状态）。 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  onUnauthorized = handler
}

/** 非请求路径上发现会话失效（如实时连接收到 unauthorized）时，走同一套处理。 */
export function notifyUnauthorized(): void {
  onUnauthorized()
}

export interface RequestOptions {
  method?: 'GET' | 'POST'
  /** POST 的 JSON 请求体；传入即视为 POST。 */
  body?: unknown
  query?: Record<string, string | number | undefined>
}

/**
 * 唯一的网络入口：JSON、同源 Cookie、信封解包。
 * 除 invalid_credentials（密码错误）外的 401 表示会话失效，会触发统一的回登录页处理。
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? (options.body !== undefined ? 'POST' : 'GET')
  let url = `/webui/api${path}`
  if (options.query) {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(options.query)) {
      if (value !== undefined) params.set(key, String(value))
    }
    const text = params.toString()
    if (text) url += `?${text}`
  }

  let response: Response
  try {
    response = await fetch(url, {
      method,
      cache: 'no-store',
      credentials: 'same-origin',
      headers: method === 'POST' ? { 'Content-Type': 'application/json' } : undefined,
      body: method === 'POST' ? JSON.stringify(options.body ?? {}) : undefined,
      signal: AbortSignal.timeout(TIMEOUT_MS),
    })
  } catch (error) {
    const timeout = error instanceof DOMException && error.name === 'TimeoutError'
    throw new ApiError(timeout ? '请求超时，请稍后重试' : '无法连接到面板服务')
  }

  let payload: ApiEnvelope<T> | null = null
  try {
    payload = (await response.json()) as ApiEnvelope<T>
  } catch {
    // 非 JSON 响应按 HTTP 状态报告。
  }
  if (response.ok && payload?.ok) return payload.data

  const detail = payload && !payload.ok ? payload.error : undefined
  const header = Number(response.headers.get('Retry-After'))
  const retry = detail?.retry_after ?? (Number.isFinite(header) && header > 0 ? header : null)
  const error = new ApiError(detail?.message || `HTTP ${response.status}`, {
    status: response.status,
    code: detail?.code,
    retryAfter: retry,
  })
  if (response.status === 401 && error.code !== 'invalid_credentials') onUnauthorized()
  throw error
}
