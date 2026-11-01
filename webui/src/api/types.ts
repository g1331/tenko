/**
 * WebUI 接口的载荷类型。后端契约（tenko/webui/contracts.py）生成的类型在 generated.ts，
 * 这里重新导出；本文件只保留尚未迁移到契约的类型和前端自用的类型。
 * 时间字段均为 ISO 8601 UTC 字符串，群 ID / 账号 ID 一律是字符串。
 */

import type { FeatureInfo, PluginIconRef } from './generated'

export * from './generated'

export interface ApiErrorBody {
  code: string
  message: string
  retry_after?: number
  [key: string]: unknown
}
export type ApiEnvelope<T> = { ok: true; data: T } | { ok: false; error: ApiErrorBody }

// ---------------------------------------------------------------- 账号与群

/** 已知策略为 random / deterministic，其余值按“其他”展示。 */
export type ResponseStrategyName = 'random' | 'deterministic' | (string & {})
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
  local?: boolean
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
// ---------------------------------------------------------------- 管理接口 /manage/*

// ---------------------------------------------------------------- 黑名单 /manage/blacklist

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
  /** 已保存但要重启才生效的字段 key；"*" 表示整份配置。 */
  restart_fields?: string[]
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
  /** 目录最近一次实测所用的 Tenko 版本和时间；从未测过为 null。 */
  tested_on?: string | null
  tested_at?: string | null
}
export interface MarketPlugin {
  id: string
  name: string
  description: string
  authors: string[]
  homepage: string
  license: string
  /** 分类的显示名；旧目录没有分类时为 null。 */
  category?: string | null
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
  /** 目录定义的分类显示名，按展示顺序。 */
  categories?: string[]
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
  /** type 为 array 时元素的类型；面板只编辑字符串列表。 */
  items?: { type?: string }
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
  /** 当前版本之后到候选版本的各版发布说明（新到旧），取自 GitHub Releases。 */
  changelog?: ReleaseNote[]
  /** 读取发布说明失败的原因；不影响检查结果。 */
  changelog_error?: string | null
}
export interface ReleaseNote {
  tag: string
  name: string
  published_at: string | null
  prerelease: boolean
  /** GitHub 上的发布页。 */
  url: string | null
  /** Markdown 原文。 */
  body: string
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
  /** 当前版本在 GitHub 上的发布页；升级源不在 GitHub 上时为 null。 */
  release_url?: string | null
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

// ---------------------------------------------------------------- 调用统计 /manage/stats
