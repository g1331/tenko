/**
 * WebUI 接口的载荷类型。后端契约（tenko/webui/contracts.py）生成的类型在 generated.ts，
 * 这里重新导出；本文件只保留尚未迁移到契约的类型和前端自用的类型。
 * 时间字段均为 ISO 8601 UTC 字符串，群 ID / 账号 ID 一律是字符串。
 */

import type { PluginConfigResponse } from './generated'

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

/** 插件配置表单里一项的 JSON Schema（pydantic 生成，面板只用到这些键）。 */
export interface ConfigField {
  type?: string
  /** type 为 array 时元素的类型；面板只编辑字符串列表。 */
  items?: { type?: string }
  title?: string
  description?: string
  default?: unknown
  enum?: (string | number | boolean)[]
}
// ---------------------------------------------------------------- 调用统计 /manage/stats

/** 契约里的 schema 是任意 JSON Schema，这里补上面板用到的 properties 结构。 */
export type PluginConfigData = Omit<PluginConfigResponse, 'schema'> & {
  schema?: { properties?: Record<string, ConfigField> }
}
