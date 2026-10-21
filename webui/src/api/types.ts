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
  /** builtin 内置；external 第三方（tenko.plugins 入口，默认关闭）。 */
  source: 'builtin' | 'external' | 'unmanaged'
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
  /** 第三方插件已启用但尚未加载，需重启后生效。 */
  restart_required: boolean
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

export type FriendRequestMode = 'off' | 'admins' | 'all'
export interface SettingsData {
  default_enabled: boolean
  stored: boolean
  friend_request_mode: FriendRequestMode
}
export interface SettingsBody {
  default_enabled?: boolean
  friend_request_mode?: FriendRequestMode
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

export type PluginTier = 'official' | 'listed'
export type PluginTrust = PluginTier | 'unlisted'
export interface MarketVersion {
  version: string
  requires_api: string
  /** 不能安装的原因；null 表示可安装。 */
  problem: string | null
}
export interface MarketPlugin {
  id: string
  name: string
  description: string
  authors: string[]
  homepage: string
  license: string
  tags: string[]
  tier: PluginTier
  icon_url: string | null
  source: string
  source_type: string
  /** 宿主可安装的最新版本；没有则看 problem。 */
  latest: string | null
  problem: string | null
  versions: MarketVersion[]
  installed_version: string | null
  blocked: string | null
}
/** 目录不可用时只有 available / error / plugins。 */
export interface MarketData {
  available: boolean
  error?: string
  url?: string
  host_api?: string
  plugins: MarketPlugin[]
}
export interface InstalledPlugin {
  plugin_id: string
  dist: string
  version: string
  source: string
  trust: PluginTrust
  requirement: string
  enabled: boolean
  loaded: boolean
  restart_required: boolean
  installed_at: string
  updated_at: string
  name: string | null
  icon_url: string | null
  /** 目录里可更新到的版本。 */
  update: string | null
  /** 当前版本被撤回的原因。 */
  yanked: string | null
  /** 被目录封禁的原因。 */
  blocked: string | null
}

export type PluginTaskAction = 'install' | 'update' | 'uninstall' | 'rollback' | 'sync'
export interface PluginTaskBody {
  action: PluginTaskAction
  plugin_id?: string
  version?: string
  clear_data?: boolean
  confirm: true
}
export interface PluginChange {
  plugin_id: string
  dist: string
  from: string | null
  to: string | null
}
export interface PluginTask {
  id: string
  action: PluginTaskAction
  plugin_id: string | null
  state: 'running' | 'succeeded' | 'failed'
  message: string
  changes: PluginChange[]
  /** 失败时环境是否已恢复；成功或未判断时为 null。 */
  restored: boolean | null
  started_at: number
  finished_at: number | null
}
export interface InstalledData {
  plugins: InstalledPlugin[]
  /** 环境与插件锁不一致的包。 */
  drift: string[]
  rollback_available: boolean
  restart_required: boolean
  can_restart: boolean
  task: PluginTask | null
}

/** 插件配置表单；schema 是 pydantic 生成的 JSON Schema（只用到 properties）。 */
export interface ConfigField {
  type?: string
  title?: string
  description?: string
  default?: unknown
  enum?: (string | number | boolean)[]
}
export interface PluginConfigData {
  plugin_id: string
  available: boolean
  schema?: { properties?: Record<string, ConfigField> }
  values?: Record<string, unknown>
  editable?: string[]
  read_only_reason?: string | null
  restart_required?: boolean
}
