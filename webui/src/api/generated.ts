/**
 * 由 tenko/webui/contracts.py 生成，不要手工修改。
 * 修改契约后在 webui/ 运行 npm run gen:types 并提交本文件。
 */

export type AccountAction = 'preferences' | 'enable' | 'disable' | 'kick' | 'forget'
export type AccountActionBody = AccountPreferencesBody | AccountConfirmBody
export type SilentReason = 'blacklisted' | 'no_account' | 'target_unavailable'
export type GroupAccountState = 'available' | 'muted' | 'offline' | 'disabled'
/**
 * 没有内容的成功响应。
 */
export type Empty = Record<string, never>
export type PairingActionBody = PairingRevealBody | PairingRotateBody

/**
 * ``preferences`` 动作的请求体：别名和能力覆盖。
 */
export interface AccountPreferencesBody {
  alias?: string
  capabilities?: CapabilityOverrides
}
export interface CapabilityOverrides {
  [k: string]: boolean
}
/**
 * ``enable`` / ``disable`` / ``kick`` / ``forget`` 动作的请求体。
 */
export interface AccountConfirmBody {
  confirm: true
}
export interface AccountActionResult {
  action: AccountAction
  platform: string
  id: string
}
export interface AccountSummary {
  id: string
  /**
   * 写接口里引用该账号的键；OneBot 下等于 id。
   */
  ref: string
  platform: string
  /**
   * 协议端昵称；尚未取到时为 null。
   */
  name: string | null
  online: boolean
  group_count: number
}
/**
 * 修改用户名和/或密码；二者都不传时后端只校验当前密码。
 */
export interface AccountUpdateBody {
  current_password: string
  username?: string
  new_password?: string
}
export interface AccountUpdateResult {
  username: string
}
export interface AccountsData {
  accounts: AccountSummary[]
  groups: GroupStatus[]
}
export interface GroupStatus {
  group_id: string
  /**
   * 已知策略为 random / deterministic，其余值按“其他”展示。
   */
  strategy: string
  /**
   * 定向模式下负责应答的账号（ref）。
   */
  deterministic_account: string | null
  silent_reason: SilentReason | null
  /**
   * 按绑定顺序排列，包含本次运行尚未连接的账号。
   */
  accounts: GroupAccount[]
}
export interface GroupAccount {
  ref: string
  id: string
  platform: string
  name: string | null
  state: GroupAccountState
  /**
   * 只在执行过管理动作后才知道。
   */
  role: ('owner' | 'admin' | 'member') | null
  /**
   * 禁言到期时间；永久禁言或未禁言为 null。
   */
  muted_until: string | null
}
export interface AuthState {
  authenticated: boolean
  username: string | null
  password_set: boolean
  expires_at: string | null
}
export interface GroupResponseBody {
  strategy: 'random' | 'deterministic'
  /**
   * 仅定向可非空；null 表示默认（第一个绑定的）账号。
   */
  account: string | null
}
export interface GroupResponseResult {
  group_id: string
  strategy: 'random' | 'deterministic'
  deterministic_account: string | null
}
export interface HostResources {
  cpu_percent: number
  memory_used: number
  memory_total: number
  memory_percent: number
  disk_used: number
  disk_total: number
  disk_percent: number
  net_sent: number
  net_received: number
}
export interface LoginBody {
  username: string
  password: string
}
export interface LoginResult {
  username: string
  expires_at: string
}
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
  /**
   * 协议端当前是否持有连接。
   */
  connected: boolean
  /**
   * 展示名（协议端用户名，缺省为账号 ID）。
   */
  name: string
  /**
   * 仅 QQ（qq-client）账号可管理。
   */
  manageable: boolean
}
export interface ManagedAccountsData {
  accounts: ManagedAccount[]
  /**
   * 可覆盖的能力名，如 send_group_message、member_mute……
   */
  capabilities: string[]
}
export interface MessageCounts {
  received_count: number
  sent_count: number
  /**
   * 最近 60 秒的收发条数。
   */
  received_rate: number
  sent_rate: number
}
export interface OverviewData {
  version: string
  uptime_seconds: number
  /**
   * Tenko 进程常驻内存（字节）；读取不到时为 null。
   */
  rss: number | null
  /**
   * 主机资源（字节 / 百分比）；未安装 psutil 等读取不到时为 null。
   */
  resources: HostResources | null
  /**
   * 近 60 分钟每分钟的 [接收, 发送]，最早的在前，最后一格是当前分钟。
   */
  activity: [number, number][]
  online_accounts: number
  active_groups: number
  /**
   * 待处理的好友申请和群邀请数。
   */
  inbox: number
  /**
   * 需要处理的插件：停用待恢复的和本次启动加载失败的。
   */
  plugin_attention?: number
  messages: MessageCounts
}
export interface PairingRevealBody {
  action: 'reveal'
}
export interface PairingRotateBody {
  action: 'rotate'
  confirm: true
}
export interface PairingInfo {
  url: string
  path: string
  /**
   * 监听 0.0.0.0 / :: 时为 true，需要用户自己填写协议端可达的主机。
   */
  requires_host: boolean
  token_configured: boolean
  can_rotate: boolean
  /**
   * 仅在 reveal / rotate 的响应中出现。
   */
  access_token?: string
}
export interface RevokeOthersResult {
  revoked: number
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
