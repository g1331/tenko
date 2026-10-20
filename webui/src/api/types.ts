/**
 * WebUI 接口的载荷类型，与 tenko/webui/service.py、auth.py 的实际输出一一对应。
 * 时间字段均为 ISO 8601 UTC 字符串，群 ID / 账号 ID 一律是字符串。
 */

export interface ApiErrorBody {
  code: string
  message: string
  retry_after?: number
  [key: string]: unknown
}
export type ApiEnvelope<T> = { ok: true; data: T } | { ok: false; error: ApiErrorBody }

// ---------------------------------------------------------------- 登录与会话

export interface AuthState {
  authenticated: boolean
  username: string | null
  password_set: boolean
  expires_at: string | null
}
export interface LoginBody {
  username: string
  password: string
}
export interface LoginResult {
  username: string
  expires_at: string
}
export interface SessionInfo {
  id: string
  created_at: string
  last_seen_at: string
  expires_at: string
  ip: string
  user_agent: string
  current: boolean
}
export interface SessionsData {
  sessions: SessionInfo[]
}
/** 修改用户名和/或密码；二者都不传时后端只校验当前密码。 */
export interface AccountUpdateBody {
  current_password: string
  username?: string
  new_password?: string
}
export interface AccountUpdateResult {
  username: string
}
export interface RevokeOthersResult {
  revoked: number
}

// ---------------------------------------------------------------- 只读数据

export interface OverviewData {
  version: string
  uptime_seconds: number
  online_accounts: number
  active_groups: number
  messages: { received_count: number; sent_count: number }
}

/** 已知策略为 random / deterministic，其余值按“其他”展示。 */
export type ResponseStrategyName = 'random' | 'deterministic' | (string & {})
export interface AccountResponseStrategy {
  group_id: string
  strategy: ResponseStrategyName
}
export interface AccountSummary {
  id: string
  platform: string
  online: boolean
  group_count: number
  response_strategies: AccountResponseStrategy[]
}
export interface AccountsData {
  accounts: AccountSummary[]
}

export type FeatureScope = 'group' | 'global' | (string & {})
export interface FeatureInfo {
  /** 插件标识，用作 /manage/plugins|features/{plugin} 的路径参数。 */
  plugin: string
  /** 展示名。 */
  name: string
  scope: FeatureScope
  /** 控制平面插件，不能通过面板停用或改开关。 */
  protected: boolean
  loaded: boolean
  enabled: boolean
  maintenance: boolean
  /** 全局覆盖：未设置为 null。 */
  global_enabled: boolean | null
  /** 群 ID → 该群当前生效的开关状态。 */
  groups: Record<string, boolean>
}
export interface FeaturesData {
  features: FeatureInfo[]
  /** 群 ID → 群名称；仅包含有可展示名称的群。 */
  group_names: Record<string, string>
}

// ---------------------------------------------------------------- 管理接口 /manage/*

/** 能力覆盖：true 强制允许，false 强制禁用，缺省表示自动探测。 */
export type CapabilityOverrides = Record<string, boolean>
export interface ManagedAccount {
  platform: string
  id: string
  alias: string
  enabled: boolean
  capabilities: CapabilityOverrides
  online: boolean
  available: boolean
  group_count: number
  groups: string[]
  /** 协议端当前是否持有连接。 */
  connected: boolean
  /** 展示名（协议端用户名，缺省为账号 ID）。 */
  name: string
  /** 仅 onebot 账号可管理。 */
  manageable: boolean
}
export interface ManagedAccountsData {
  accounts: ManagedAccount[]
  /** 可覆盖的能力名，如 send_group_message、member_mute…… */
  capabilities: string[]
}
export type AccountAction = 'preferences' | 'enable' | 'disable' | 'kick' | 'forget'
export type AccountActionBody =
  { alias?: string; capabilities?: CapabilityOverrides } | { confirm: true }
export interface AccountActionResult {
  action: AccountAction
  platform: string
  id: string
}

export interface PairingInfo {
  url: string
  path: string
  /** 监听 0.0.0.0 / :: 时为 true，需要用户自己填写协议端可达的主机。 */
  requires_host: boolean
  token_configured: boolean
  can_rotate: boolean
  /** 仅在 reveal / rotate 的响应中出现。 */
  access_token?: string
}
export type PairingActionBody = { action: 'reveal' } | { action: 'rotate'; confirm: true }

export interface PluginToggleBody {
  enabled: boolean
  confirm: true
}
export interface PluginToggleResult {
  plugin: string
  enabled: boolean
}

/** POST /manage/features/{plugin} 的三种请求体：维护开关、全局开关（null 表示清除）、群级开关。 */
export type FeatureToggleBody =
  | { maintenance: boolean }
  | { group_id: null; enabled: boolean | null }
  | { group_id: string; enabled: boolean }
export interface FeatureToggleResult {
  plugin: string
  state: { maintenance?: boolean; global_enabled?: boolean; groups?: Record<string, boolean> }
}

export interface SettingsData {
  default_enabled: boolean
  stored: boolean
}
export interface SettingsBody {
  default_enabled: boolean
}

export type LogLevel = 'INFO' | 'SUCCESS' | 'WARNING' | 'ERROR' | 'CRITICAL'
export interface LogRecord {
  id: number
  time: string
  level: string
  message: string
}
export interface LogQuery {
  q?: string
  /** 历史日志文件名；传入后返回 LogHistoryData，否则为实时日志。 */
  file?: string
  /** 实时日志游标：只返回 id 大于该值的记录。 */
  after?: number
  level?: LogLevel | ''
}
interface LogCommon {
  files: string[]
  history_enabled: boolean
}
export interface LogLiveData extends LogCommon {
  records: LogRecord[]
  cursor: number
  reset: boolean
  truncated: boolean
}
export interface LogHistoryData extends LogCommon {
  lines: string[]
  truncated: boolean
  matched: number
  scan_limit_bytes: number
}
export type LogData = LogLiveData | LogHistoryData
