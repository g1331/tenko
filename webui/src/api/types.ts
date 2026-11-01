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

// ---------------------------------------------------------------- 日志 /manage/logs

export type LogLevel = 'INFO' | 'SUCCESS' | 'WARNING' | 'ERROR' | 'CRITICAL'
export interface LogQuery {
  q?: string
  /** 历史日志文件名；传入后返回 LogHistoryData，否则为实时日志。 */
  file?: string
  /** 实时日志游标：只返回 id 大于该值的记录。 */
  after?: number
  level?: LogLevel | ''
}
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

/** 契约里的 schema 是任意 JSON Schema，这里补上面板用到的 properties 结构。 */
export type PluginConfigData = Omit<PluginConfigResponse, 'schema'> & {
  schema?: { properties?: Record<string, ConfigField> }
}
