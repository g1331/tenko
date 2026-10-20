import type { FeatureInfo, FeaturesData } from '@/api'
import { isSwitchable } from '@/composables/status'

/** 功能开关页的纯数据逻辑：群 / 插件模型、可调判定和路由 query 的解析。 */

export interface GroupRef {
  id: string
  name: string
}
export interface SwitchModel {
  features: FeatureInfo[]
  /** 全部出现过的群，按 ID 长度、再按字典序排序。 */
  groups: GroupRef[]
  groupFeatures: FeatureInfo[]
  globalFeatures: FeatureInfo[]
}

export type SwitchView = 'group' | 'plugin'
export type GroupFilter = 'all' | 'on' | 'off'
export interface SwitchState {
  view: SwitchView
  /** 选中的群 ID / 插件标识；为空或失效时取列表第一项。 */
  id: string
  /** 左侧列表搜索。 */
  q: string
  /** 插件详情内的群搜索。 */
  gq: string
  f: GroupFilter
}

export function buildModel(data: FeaturesData): SwitchModel {
  const features = data.features ?? []
  const names = data.group_names ?? {}
  const ids = new Set<string>()
  for (const feature of features) for (const id of Object.keys(feature.groups ?? {})) ids.add(id)
  const groups = [...ids]
    .sort((a, b) => a.length - b.length || (a < b ? -1 : a > b ? 1 : 0))
    .map((id) => ({ id, name: names[id] ?? '' }))
  return {
    features,
    groups,
    groupFeatures: features.filter((item) => item.scope !== 'global'),
    globalFeatures: features.filter((item) => item.scope === 'global'),
  }
}

export const groupLabel = (group: GroupRef) => group.name || group.id
export const featureLabel = (feature: FeatureInfo) => feature.name || feature.plugin
export const isOn = (feature: FeatureInfo, groupId: string) => feature.groups?.[groupId] === true

/** 一个群里“可调功能”的开启数；total 为 0 表示该群没有可调功能。 */
export function groupStat(model: SwitchModel, groupId: string) {
  const switchable = model.groupFeatures.filter(isSwitchable)
  return { on: switchable.filter((item) => isOn(item, groupId)).length, total: switchable.length }
}

export function matches(query: string, ...values: Array<string | undefined>): boolean {
  const needle = query.trim().toLowerCase()
  return !needle || values.some((value) => (value ?? '').toLowerCase().includes(needle))
}

export function pickSelected<T>(items: T[], id: string, key: (item: T) => string): T | null {
  return items.find((item) => key(item) === id) ?? items[0] ?? null
}

/** 插件详情里按搜索和状态筛选后的群。 */
export function visibleGroups(feature: FeatureInfo, model: SwitchModel, state: SwitchState) {
  return model.groups.filter(
    (group) =>
      matches(state.gq, group.id, group.name) &&
      (state.f === 'all' || isOn(feature, group.id) === (state.f === 'on')),
  )
}

/** 全局功能当前显示的状态文案：有全局覆盖时是开 / 关，否则沿用默认。 */
export function globalMode(feature: FeatureInfo): 'on' | 'off' | 'default' {
  return feature.global_enabled === true
    ? 'on'
    : feature.global_enabled === false
      ? 'off'
      : 'default'
}

// ---------------------------------------------------------------- 路由 query

type Query = Record<string, string | null | Array<string | null> | undefined>

const first = (value: Query[string]) => (Array.isArray(value) ? value[0] : value) ?? ''

export const DEFAULT_STATE: SwitchState = { view: 'group', id: '', q: '', gq: '', f: 'all' }

export function parseQuery(query: Query): SwitchState {
  const f = first(query.f)
  return {
    view: first(query.view) === 'plugin' ? 'plugin' : 'group',
    id: first(query.id),
    q: first(query.q),
    gq: first(query.gq),
    f: f === 'on' || f === 'off' ? f : 'all',
  }
}

/** 只写入非默认值，保持 URL 简短；view 始终保留，便于分享。 */
export function serializeQuery(state: SwitchState): Record<string, string> {
  const query: Record<string, string> = { view: state.view }
  if (state.id) query.id = state.id
  if (state.q) query.q = state.q
  if (state.gq) query.gq = state.gq
  if (state.f !== 'all') query.f = state.f
  return query
}

export const sameState = (a: SwitchState, b: SwitchState) =>
  a.view === b.view && a.id === b.id && a.q === b.q && a.gq === b.gq && a.f === b.f

/** 全局覆盖的标签：有覆盖时是“全局 开 / 关”；未设置时标出沿用默认后的实际状态。 */
export function globalChip(feature: FeatureInfo): { kind: 'on' | 'off'; text: string } {
  if (feature.global_enabled != null)
    return {
      kind: feature.global_enabled ? 'on' : 'off',
      text: `全局 ${feature.global_enabled ? '开' : '关'}`,
    }
  const first = Object.values(feature.groups ?? {})[0]
  return {
    kind: first === false ? 'off' : 'on',
    text: `跟随默认${first === undefined ? '' : first ? ' · 开' : ' · 关'}`,
  }
}
