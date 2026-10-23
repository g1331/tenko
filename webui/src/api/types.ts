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
  /** Tenko 进程常驻内存（字节）；读取不到时为 null。 */
  rss: number | null
  /** 主机资源（字节 / 百分比）；未安装 psutil 等读取不到时为 null。 */
  resources: HostResources | null
  /** 近 60 分钟每分钟的 [接收, 发送]，最早的在前，最后一格是当前分钟。 */
  activity: [number, number][]
  online_accounts: number
  active_groups: number
  /** 待处理的好友申请和群邀请数。 */
  inbox: number
  /** 需要处理的插件：停用待恢复的和本次启动加载失败的。 */
  plugin_attention?: number
  messages: {
    received_count: number
    sent_count: number
    /** 最近 60 秒的收发条数。 */
    received_rate: number
    sent_rate: number
  }
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

/** 已知策略为 random / deterministic，其余值按“其他”展示。 */
export type ResponseStrategyName = 'random' | 'deterministic' | (string & {})
export interface AccountSummary {
  id: string
  /** 写接口里引用该账号的键；OneBot 下等于 id。 */
  ref: string
  platform: string
  /** 协议端昵称；尚未取到时为 null。 */
  name: string | null
  online: boolean
  group_count: number
}
/** 账号在某群的状态，优先级：停用 > 离线 > 禁言。 */
export type GroupAccountState = 'available' | 'muted' | 'offline' | 'disabled'
export interface GroupAccount {
  ref: string
  id: string
  platform: string
  name: string | null
  state: GroupAccountState
  /** 只在执行过管理动作后才知道。 */
  role: 'owner' | 'admin' | 'member' | null
  /** 禁言到期时间；永久禁言或未禁言为 null。 */
  muted_until: string | null
}
/** 群当前不会响应的原因：群在黑名单、没有可用账号，或定向账号不可用。 */
export type SilentReason = 'blacklisted' | 'no_account' | 'target_unavailable'
export interface GroupStatus {
  group_id: string
  strategy: ResponseStrategyName
  /** 定向模式下负责应答的账号（ref）。 */
  deterministic_account: string | null
  silent_reason: SilentReason | null
  /** 按绑定顺序排列，包含本次运行尚未连接的账号。 */
  accounts: GroupAccount[]
}
export interface AccountsData {
  accounts: AccountSummary[]
  groups: GroupStatus[]
}

export type FeatureScope = 'group' | 'global' | (string & {})
export interface FeatureInfo {
  /** 插件标识，用作 /manage/plugins|features/{plugin} 的路径参数。 */
  plugin: string
  /** 展示名。 */
  name: string
  description: string
  /** 未在插件目录登记的残留开关记录没有图标。 */
  icon: PluginIconRef | null
  scope: FeatureScope
  /** builtin 内置；external 扩展插件（tenko.plugins 入口，默认关闭）。 */
  source: 'builtin' | 'external' | 'unmanaged'
  /** 控制平面插件，不能通过面板停用或改开关。 */
  protected: boolean
  /** 受控插件：默认关闭，群里需 BOT 管理员开启，私聊仅 BOT 管理员可用。 */
  controlled: boolean
  /** 插件自己声明的受控状态；与 controlled 不同时说明 Master 在面板改过。 */
  controlled_declared: boolean
  loaded: boolean
  enabled: boolean
  maintenance: boolean
  /** 全局覆盖：未设置为 null。 */
  global_enabled: boolean | null
  /** 群 ID → 该群当前生效的开关状态。 */
  groups: Record<string, boolean>
}
/** glyph 为单色 SVG（按主题着色），否则是目录里的 PNG 图标。 */
export interface PluginIconRef {
  url: string
  glyph: boolean
}
export interface PluginLink {
  kind: 'home' | 'repo' | 'pypi' | 'link'
  label: string
  url: string
}
export interface PluginCommandDoc {
  /** 带前缀的命令名，如 /帮助。 */
  title: string
  /** 说明、用法、示例、选项、权限；与群里 /帮助 编号 的内容一致。 */
  fields: { label: string; value: string }[]
}
/** 插件详情页的说明资料；状态和开关仍以 FeatureInfo 为准。 */
export interface PluginDetail {
  plugin: string
  name: string
  source: FeatureInfo['source']
  description: string
  /** 内置插件为 Tenko 版本，扩展插件为已安装版本。 */
  version: string | null
  authors: string[]
  license: string | null
  tier: string | null
  tags: string[]
  icon: PluginIconRef
  links: PluginLink[]
  /** 插件未加载时为空。 */
  commands: PluginCommandDoc[]
  installed: Omit<InstalledPlugin, 'name' | 'icon_url' | 'update' | 'yanked' | 'blocked'> | null
  has_config: boolean
  update: string | null
  yanked: string | null
  blocked: string | null
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
  /** 仅 QQ（qq-client）账号可管理。 */
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
  /** 扩展插件已启用但尚未加载，需重启后生效。 */
  restart_required: boolean
  /** 首次启用的扩展插件没能直接加载的原因。 */
  restart_reason?: string | null
}

/** POST /manage/features/{plugin} 的三种请求体：维护开关、全局开关（null 表示清除）、群级开关。 */
export type FeatureToggleBody =
  | { maintenance: boolean }
  /** null 恢复插件声明的受控状态。 */
  | { controlled: boolean | null }
  | { group_id: null; enabled: boolean | null }
  | { group_id: string; enabled: boolean }
export interface FeatureToggleResult {
  plugin: string
  state?: { maintenance?: boolean; global_enabled?: boolean; groups?: Record<string, boolean> }
  controlled?: boolean
}

// ---------------------------------------------------------------- 黑名单 /manage/blacklist

export type BlacklistKind = 'user' | 'group'
export interface BlacklistEntry {
  platform: string
  id: string
  /** 群名称（取自聊天记录），用户条目为空。 */
  name: string
  reason: string
  /** 命令操作人的用户 ID；面板操作为 webui。 */
  operator_id: string
  created_at: string | null
}
export interface BlacklistData {
  /** 权限状态是否已从数据库加载；未就绪时不能修改。 */
  ready: boolean
  users: BlacklistEntry[]
  groups: BlacklistEntry[]
  /** 可选的平台：已知账号的平台和已有条目的平台。 */
  platforms: string[]
}
export interface BlacklistBody {
  kind: BlacklistKind
  platform: string
  id: string
  blacklisted: boolean
  reason?: string
}

export interface GroupResponseBody {
  strategy: 'random' | 'deterministic'
  /** 仅定向可非空；null 表示默认（第一个绑定的）账号。 */
  account: string | null
}
export interface GroupResponseResult {
  group_id: string
  strategy: 'random' | 'deterministic'
  deterministic_account: string | null
}

export type FriendRequestMode = 'off' | 'admins' | 'all'
/** 给 Master 的通知：发往私聊还是通知群，由哪个账号发送（null 表示任选在线账号）。 */
export interface NotifySettings {
  mode: 'private' | 'group'
  group: string | null
  account: string | null
}
export interface SettingsData {
  default_enabled: boolean
  stored: boolean
  friend_request_mode: FriendRequestMode
  notify: NotifySettings
}
export interface SettingsBody {
  default_enabled?: boolean
  friend_request_mode?: FriendRequestMode
  notify?: Partial<NotifySettings>
}

export type CoreConfigKind = 'boolean' | 'integer' | 'number' | 'enum' | 'identifier' | 'urls'
export type CoreConfigGroup =
  'basic' | 'upgrade' | 'render' | 'ratelimit' | 'chat_log' | 'exception' | 'registry'
/** 宿主配置项；value 的类型随 kind：boolean / 数字 / 选项值 / 字符串 / 字符串数组。 */
export interface CoreConfigField {
  key: string
  group: CoreConfigGroup
  label: string
  kind: CoreConfigKind
  default: unknown
  help: string
  options: { value: string | number; label: string }[]
  min: number | null
  max: number | null
  value: unknown
  /** 值引用了环境变量，只能在配置文件里改。 */
  read_only: boolean
}
export interface CoreConfigData {
  fields: CoreConfigField[]
  writable: boolean
  restart_required: boolean
  can_restart: boolean
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

export type PluginTaskAction = 'install' | 'update' | 'resume' | 'uninstall' | 'rollback' | 'sync'
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
  /** 更新和恢复：每个插件仍需重启的原因，null 表示已在运行中生效。 */
  live?: Record<string, string | null>
  /** 这次变更是否要重启才能生效。 */
  restart_required?: boolean
  started_at: number
  finished_at: number | null
}
/** 因与宿主不兼容而停用的插件：包已移出环境，记录、数据和配置保留。 */
export interface SuspendedPlugin {
  plugin_id: string
  name: string | null
  icon_url: string | null
  dist: string
  /** 停用前的版本。 */
  version: string
  trust: PluginTrust
  /** 停用前是否启用；恢复后沿用。 */
  enabled: boolean
  reason: string
  suspended_at: string | null
  /** 已选定、下次启动自动装回的来源。 */
  resume: string | null
  /** 上次自动恢复失败的原因。 */
  error: string | null
  /** 目录里能装在当前宿主上的版本（新到旧）；未收录的插件为空，按原来源恢复。 */
  options: string[]
}
/** 本次启动加载失败的插件；失败只跳过该插件。 */
export interface PluginFailure {
  plugin_id: string
  name: string | null
  builtin: boolean
  reason: string
}
export interface InstalledData {
  plugins: InstalledPlugin[]
  suspended?: SuspendedPlugin[]
  failures?: PluginFailure[]
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
}

/** 宿主升级页；时间均为 ISO 8601 UTC。 */
export interface UpgradeCheck {
  checked_at: string
  status: string
  current_version: string
  channel: string
  candidate: {
    tag: string
    version: string
    prerelease: boolean
    source: string
    notes: string | null
  } | null
}
export type UpgradeAction = 'upgrade' | 'rollback'
export interface UpgradeJob {
  action: UpgradeAction
  state: 'running' | 'restarting' | 'succeeded' | 'failed'
  stage: string
  message: string
  target: string | null
  /** 与目标版本不兼容的插件及原因；"*" 表示插件组合冲突。 */
  incompatible: Record<string, string>
  /** 不兼容插件在目标版本上可装的目录版本（新到旧）；空数组表示只能停用。 */
  options?: Record<string, string[]>
}
export interface UpgradeHistoryEntry {
  timestamp: string | null
  action: 'install' | 'rollback' | 'startup' | 'download' | 'restart'
  result: string
  from: string | null
  to: string | null
  automatic: boolean
  error: string | null
}
export interface UpgradeStatus {
  enabled: boolean
  current_version: string
  channel: 'stable' | 'prerelease'
  policy: 'check' | 'download' | 'install'
  check_interval_hours: number
  previous_version: string | null
  last_check: UpgradeCheck | null
  job: UpgradeJob | null
  history: UpgradeHistoryEntry[]
}
export interface UpgradeStartBody {
  action: UpgradeAction
  confirm: true
  /** 每个不兼容插件的处理："suspend" 或目标版本上要装的版本号。 */
  plugins?: Record<string, string>
}

/** 待处理的好友申请和群邀请；时间为 ISO 8601 UTC。 */
export interface InboxItem {
  id: string
  kind: 'friend' | 'group'
  platform: string
  /** 收到请求的 bot 账号。 */
  account_id: string
  user_id: string | null
  user_name: string
  comment: string
  group_id: string | null
  group_name: string | null
  created_at: string
  /** 超时自动拒绝的时间；好友申请没有。 */
  expires_at: string | null
}
export interface InboxData {
  items: InboxItem[]
}
export type InboxAction = 'approve' | 'reject' | 'dismiss'

/** 聊天消息段：后端把 Satori 元素归一化成这几种，未知元素是 other 占位。 */
export type ChatSegment =
  | { type: 'text'; text: string }
  | { type: 'at'; id: string; name: string }
  | { type: 'face'; id: string; name: string }
  /** src 为空表示图片没有可用地址（例如渲染出的内嵌图片不入库）。 */
  | { type: 'image'; src: string }
  | { type: 'quote'; id: string; user_id: string; name: string; text: string }
  | { type: 'other'; name: string }
export interface ChatMessage {
  /** 记录编号，用作翻页和增量读取的游标。 */
  id: number
  /** 平台消息 ID，回复时引用它。 */
  message_id: string | null
  outgoing: boolean
  user_id: string
  user_name: string
  content: ChatSegment[]
  created_at: string
}
export type ChatType = 'group' | 'private'
export interface ChatConversation {
  /** 群号，或 private:<QQ号>。 */
  channel_id: string
  chat_type: ChatType
  /** 群号或私聊对方的 QQ 号。 */
  peer_id: string
  name: string
  last: { preview: string; user_name: string; outgoing: boolean; created_at: string } | null
  muted: boolean
}
export interface ChatConversationsData {
  /** 消息记录是否开启；关闭时仍可发送，但看不到历史。 */
  enabled: boolean
  retention_days: number
  online: boolean
  conversations: ChatConversation[]
}
export interface ChatMessagesData {
  messages: ChatMessage[]
  has_more: boolean
}
/** 发送时可用的消息段；回复只能在最前面。 */
export type ChatSendSegment =
  | { type: 'text'; text: string }
  | { type: 'at'; id: string; name?: string }
  | { type: 'image'; src: string }
  | { type: 'quote'; id: string }
export type SandboxChat = 'group' | 'private'
export type SandboxIdentity = 'member' | 'admin' | 'owner' | 'master'
/** 命令沙盒：面板扮演用户和 bot 对话，记录只在内存里。 */
export interface SandboxData {
  ready: boolean
  bot: { id: string; name: string }
  group: { id: string; name: string }
  identities: { value: SandboxIdentity; user_id: string; name: string }[]
  messages: ChatMessage[]
}
export interface SandboxSendBody {
  chat: SandboxChat
  identity: SandboxIdentity
  content: ChatSendSegment[]
}
export interface ChatSendBody {
  platform: string
  account: string
  channel: string
  content: ChatSendSegment[]
}
