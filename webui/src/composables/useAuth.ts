import { computed, reactive } from 'vue'
import { api, ApiError, setUnauthorizedHandler, type AuthState, type LoginResult } from '@/api'

/**
 * 面板登录状态。只有 /auth/state 会在登录前请求；路由守卫在首次导航时调用 ensureLoaded()。
 * `loaded` 为 false 表示尚未问过后端，守卫据此决定是否需要等待。
 */
const state = reactive({
  loaded: false,
  authenticated: false,
  username: '',
  expiresAt: null as string | null,
  /** 后端是否已设置面板账号密码（未设置时登录页展示设置说明）。 */
  passwordSet: true,
  /** /auth/state 本身读取失败时的错误文案；登录页据此给出重试入口。 */
  stateError: '',
})

function apply(data: AuthState): void {
  state.loaded = true
  state.stateError = ''
  state.authenticated = data.authenticated
  state.username = data.username ?? ''
  state.expiresAt = data.expires_at
  state.passwordSet = data.password_set !== false
}

/** 重新向后端确认登录状态；读取失败视为未登录并记录错误。 */
async function refresh(): Promise<void> {
  try {
    apply(await api.auth.state())
  } catch (error) {
    state.loaded = true
    state.authenticated = false
    state.username = ''
    state.expiresAt = null
    state.stateError = error instanceof ApiError ? error.message : String(error)
  }
}

let pending: Promise<void> | null = null
function ensureLoaded(): Promise<void> {
  if (state.loaded) return Promise.resolve()
  pending ??= refresh().finally(() => (pending = null))
  return pending
}

function signIn(result: LoginResult, fallbackName: string): void {
  state.loaded = true
  state.stateError = ''
  state.authenticated = true
  state.username = result.username || fallbackName
  state.expiresAt = result.expires_at
  state.passwordSet = true
}

/** 清除本地登录态（登出或会话失效）；不请求后端。 */
function signOut(): void {
  state.authenticated = false
  state.username = ''
  state.expiresAt = null
}

export function useAuth() {
  return {
    state,
    isAuthenticated: computed(() => state.authenticated),
    ensureLoaded,
    refresh,
    signIn,
    signOut,
    login: async (username: string, password: string) => {
      const result = await api.auth.login({ username, password })
      signIn(result, username)
    },
    logout: async () => {
      await api.auth.logout()
      signOut()
    },
    /** 账号页改名后同步面板右上角显示的用户名。 */
    setUsername: (name: string) => (state.username = name),
  }
}

/** 注册 401 → 清除登录态的全局处理；`onExpired` 由路由层提供跳转逻辑。 */
export function installAuthGuard(onExpired: () => void): void {
  setUnauthorizedHandler(() => {
    if (!state.authenticated) return
    signOut()
    onExpired()
  })
}
