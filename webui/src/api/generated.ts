/**
 * 由 tenko/webui/contracts.py 生成，不要手工修改。
 * 修改契约后在 webui/ 运行 npm run gen:types 并提交本文件。
 */

export type AccountAction = 'preferences' | 'enable' | 'disable' | 'kick' | 'forget'
export type AccountActionBody = AccountPreferencesBody | AccountConfirmBody
export type SilentReason = 'blacklisted' | 'no_account' | 'target_unavailable'
export type GroupAccountState = 'available' | 'muted' | 'offline' | 'disabled'
export type BlacklistKind = 'user' | 'group'
export type ChatType = 'group' | 'private'
export type ChatSegment =
  | ChatTextSegment
  | ChatAtSegment
  | ChatFaceSegment
  | ChatImageSegment
  | ChatQuoteSegment
  | ChatOtherSegment
export type ChatSendSegment =
  ChatSendTextSegment | ChatSendAtSegment | ChatSendImageSegment | ChatSendQuoteSegment
/**
 * 没有内容的成功响应。
 */
export type Empty = Record<string, never>
export type FeatureSource = 'builtin' | 'external' | 'unmanaged'
export type FeatureToggleBody =
  FeatureMaintenanceBody | FeatureControlledBody | FeatureGlobalBody | FeatureGroupBody
export type FriendRequestMode = 'off' | 'admins' | 'all'
export type InboxAction = 'approve' | 'reject' | 'dismiss'
export type PairingActionBody = PairingRevealBody | PairingRotateBody
export type SandboxChat = 'group' | 'private'
export type SandboxIdentity = 'member' | 'admin' | 'owner' | 'master'
export type StatsDays = 1 | 7 | 30

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
export interface BlacklistBody {
  kind: BlacklistKind
  platform: string
  id: string
  blacklisted: boolean
  reason?: string
}
export interface BlacklistData {
  /**
   * 权限状态是否已从数据库加载；未就绪时不能修改。
   */
  ready: boolean
  users: BlacklistEntry[]
  groups: BlacklistEntry[]
  /**
   * 可选的平台：已知账号的平台和已有条目的平台。
   */
  platforms: string[]
}
export interface BlacklistEntry {
  platform: string
  id: string
  /**
   * 群名称（取自聊天记录），用户条目为空。
   */
  name: string
  reason: string
  /**
   * 命令操作人的用户 ID；面板操作为 webui。
   */
  operator_id: string
  created_at: string | null
}
export interface ChatAtSegment {
  type: 'at'
  id: string
  name: string
}
export interface ChatConversation {
  /**
   * 群号，或 private:<QQ号>。
   */
  channel_id: string
  chat_type: ChatType
  /**
   * 群号或私聊对方的 QQ 号。
   */
  peer_id: string
  name: string
  last: ChatConversationLast | null
  muted: boolean
}
export interface ChatConversationLast {
  preview: string
  user_name: string
  outgoing: boolean
  created_at: string
}
export interface ChatConversationsData {
  /**
   * 消息记录是否开启；关闭时仍可发送，但看不到历史。
   */
  enabled: boolean
  retention_days: number
  online: boolean
  conversations: ChatConversation[]
}
export interface ChatFaceSegment {
  type: 'face'
  id: string
  name: string
}
export interface ChatImageSegment {
  type: 'image'
  /**
   * 为空表示图片没有可用地址（例如渲染出的内嵌图片不入库）。
   */
  src: string
}
export interface ChatMessage {
  /**
   * 记录编号，用作翻页和增量读取的游标。
   */
  id: number
  /**
   * 平台消息 ID，回复时引用它。
   */
  message_id: string | null
  outgoing: boolean
  user_id: string
  user_name: string
  content: ChatSegment[]
  created_at: string
}
export interface ChatTextSegment {
  type: 'text'
  text: string
}
export interface ChatQuoteSegment {
  type: 'quote'
  id: string
  user_id: string
  name: string
  text: string
}
export interface ChatOtherSegment {
  type: 'other'
  name: string
}
export interface ChatMessagesData {
  messages: ChatMessage[]
  has_more: boolean
}
export interface ChatSendAtSegment {
  type: 'at'
  id: string
  name?: string
}
export interface ChatSendBody {
  platform: string
  account: string
  channel: string
  content: ChatSendSegment[]
}
export interface ChatSendTextSegment {
  type: 'text'
  text: string
}
export interface ChatSendImageSegment {
  type: 'image'
  src: string
}
export interface ChatSendQuoteSegment {
  type: 'quote'
  id: string
}
export interface FeatureControlledBody {
  /**
   * null 恢复插件声明的受控状态。
   */
  controlled: boolean | null
}
export interface FeatureGlobalBody {
  group_id: null
  /**
   * null 清除全局覆盖。
   */
  enabled: boolean | null
}
export interface FeatureGroupBody {
  group_id: string
  enabled: boolean
}
export interface FeatureInfo {
  /**
   * 插件标识，用作 /manage/plugins|features/{plugin} 的路径参数。
   */
  plugin: string
  /**
   * 展示名。
   */
  name: string
  description: string
  /**
   * 未在插件目录登记的残留开关记录没有图标。
   */
  icon: PluginIconRef | null
  /**
   * 开关范围：group 按群开关，global 全局开关；其余值按“其他”展示。
   */
  scope: string
  source: FeatureSource
  /**
   * 本地插件目录（.tenko/plugins/local/）里的扩展插件。
   */
  local: boolean
  /**
   * 控制平面插件，不能通过面板停用或改开关。
   */
  protected: boolean
  /**
   * 受控插件：默认关闭，群里需 BOT 管理员开启，私聊仅 BOT 管理员可用。
   */
  controlled: boolean
  /**
   * 插件自己声明的受控状态；与 controlled 不同时说明 Master 在面板改过。
   */
  controlled_declared: boolean
  loaded: boolean
  enabled: boolean
  maintenance: boolean
  /**
   * 全局覆盖：未设置为 null。
   */
  global_enabled: boolean | null
  /**
   * 群 ID → 该群当前生效的开关状态。
   */
  groups: {
    [k: string]: boolean
  }
}
/**
 * glyph 为单色 SVG（按主题着色），否则是目录里的 PNG 图标。
 */
export interface PluginIconRef {
  url: string
  glyph: boolean
}
export interface FeatureMaintenanceBody {
  maintenance: boolean
}
export interface FeatureState {
  maintenance?: boolean
  global_enabled?: boolean
  groups?: {
    [k: string]: boolean
  }
}
export interface FeatureToggleResult {
  plugin: string
  state?: FeatureState
  controlled?: boolean
}
export interface FeaturesData {
  features: FeatureInfo[]
  /**
   * 群 ID → 群名称；仅包含有可展示名称的群。
   */
  group_names: {
    [k: string]: string
  }
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
export interface InboxData {
  items: InboxItem[]
}
/**
 * 待处理的好友申请和群邀请；时间为 ISO 8601 UTC。
 */
export interface InboxItem {
  id: string
  kind: 'friend' | 'group'
  platform: string
  /**
   * 收到请求的 bot 账号。
   */
  account_id: string
  user_id: string | null
  user_name: string
  comment: string
  group_id: string | null
  group_name: string | null
  created_at: string
  /**
   * 超时自动拒绝的时间；好友申请没有。
   */
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
/**
 * 给 Master 的通知：发往私聊还是通知群，由哪个账号发送（null 表示任选在线账号）。
 */
export interface NotifySettings {
  mode: 'private' | 'group'
  group: string | null
  account: string | null
}
/**
 * 通知设置的局部修改：只写要改的字段。
 */
export interface NotifySettingsChanges {
  mode?: 'private' | 'group'
  group?: string | null
  account?: string | null
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
export interface PluginToggleBody {
  enabled: boolean
  confirm: true
}
export interface PluginToggleResult {
  plugin: string
  enabled: boolean
  /**
   * 扩展插件已启用但尚未加载，需重启后生效。
   */
  restart_required: boolean
  /**
   * 首次启用的扩展插件没能直接加载的原因。
   */
  restart_reason: string | null
}
export interface RevokeOthersResult {
  revoked: number
}
export interface SandboxBot {
  id: string
  name: string
}
export interface SandboxClearResult {
  cleared: boolean
}
/**
 * 命令沙盒：面板扮演用户和 bot 对话，记录只在内存里。
 */
export interface SandboxData {
  ready: boolean
  bot: SandboxBot
  group: SandboxGroup
  identities: SandboxIdentityInfo[]
  messages: ChatMessage[]
}
export interface SandboxGroup {
  id: string
  name: string
}
export interface SandboxIdentityInfo {
  value: SandboxIdentity
  user_id: string
  name: string
}
export interface SandboxResetResult {
  reset: boolean
}
export interface SandboxSendBody {
  chat: SandboxChat
  identity: SandboxIdentity
  content: ChatSendSegment[]
}
export interface SentResult {
  sent: boolean
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
export interface SettingsBody {
  default_enabled?: boolean
  friend_request_mode?: FriendRequestMode
  notify?: NotifySettingsChanges
}
export interface SettingsData {
  default_enabled: boolean
  stored: boolean
  friend_request_mode: FriendRequestMode
  notify: NotifySettings
}
export interface StatsData {
  days: StatsDays
  since: string
  total: StatsSummary | null
  trend: number[]
  trend_unit: 'hour' | 'day'
  plugins: StatsPlugin[]
  idle: string[]
  errors: StatsError[]
}
export interface StatsSummary {
  key: string
  calls: number
  ok: number
  errors: number
  denied: number
  success_rate: number | null
  avg_ms: number | null
  max_ms: number
  avg_render_ms: number | null
  replies: number
  reasons: StatsReason[]
  actions: {
    [k: string]: number
  }
}
export interface StatsReason {
  reason: string
  label: string
  count: number
}
export interface StatsPlugin {
  key: string
  calls: number
  ok: number
  errors: number
  denied: number
  success_rate: number | null
  avg_ms: number | null
  max_ms: number
  avg_render_ms: number | null
  replies: number
  reasons: StatsReason[]
  actions: {
    [k: string]: number
  }
  label: string
  commands: StatsSummary[]
  groups: StatsGroup[]
}
export interface StatsGroup {
  key: string
  calls: number
  ok: number
  errors: number
  denied: number
  success_rate: number | null
  avg_ms: number | null
  max_ms: number
  avg_render_ms: number | null
  replies: number
  reasons: StatsReason[]
  actions: {
    [k: string]: number
  }
  name: string
}
export interface StatsError {
  fingerprint: string
  kind: string
  location: string
  message: string
  traceback: string
  first_seen: string
  last_seen: string
  total: number
  groups: string[]
  failures: StatsFailure[]
}
export interface StatsFailure {
  id: number
  at: string
  /**
   * group / private；非消息会话（other）也会出现。
   */
  chat: string
  group_id: string
  group_name: string
  user_id: string
  preview: string
}
export interface StatsReplayResult {
  chat: SandboxChat
}
