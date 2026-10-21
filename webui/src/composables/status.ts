import type { AccountSummary, FeatureInfo } from '@/api'

/** 面板统一的状态语义；StatusTag 把它映射为 Element Plus 标签类型。 */
export type StatusKind = 'on' | 'off' | 'pending' | 'danger' | 'prot'

export interface StatusInfo {
  kind: StatusKind
  label: string
}

export function accountStatus(account: Pick<AccountSummary, 'online'>): StatusInfo {
  return account.online ? { kind: 'on', label: '在线' } : { kind: 'off', label: '离线' }
}

/** 插件的综合状态：受保护 > 未加载 > 已停用 > 维护中 > 全局开关 > 群级开关。 */
export function featureStatus(feature: FeatureInfo): StatusInfo {
  if (feature.protected) return { kind: 'prot', label: '受保护' }
  if (!feature.loaded) {
    // 第三方插件默认不加载；已启用的要等重启后才会加载。
    if (feature.source === 'external')
      return feature.enabled
        ? { kind: 'pending', label: '待重启' }
        : { kind: 'off', label: '未启用' }
    return { kind: 'pending', label: '未加载' }
  }
  if (!feature.enabled) return { kind: 'off', label: '已停用' }
  if (feature.maintenance) return { kind: 'pending', label: '维护中' }
  if (feature.global_enabled === false) return { kind: 'off', label: '全局关闭' }
  if (feature.scope === 'global') return { kind: 'on', label: '全局开启' }
  const groups = Object.values(feature.groups ?? {})
  if (!groups.length) return { kind: 'pending', label: '暂无群组' }
  return groups.some(Boolean) ? { kind: 'on', label: '运行中' } : { kind: 'off', label: '已关闭' }
}

/** 只有未受保护、未维护、未被全局策略覆盖的群级功能才允许逐群修改。 */
export function isSwitchable(feature: FeatureInfo): boolean {
  return (
    feature.scope !== 'global' &&
    !feature.protected &&
    !feature.maintenance &&
    feature.global_enabled == null
  )
}

/** 已安装但未启用（因而未加载）的第三方插件：总览不把它算作运行统计，也不是需要处理的异常。 */
export function isInactiveExternal(feature: FeatureInfo): boolean {
  return feature.source === 'external' && !feature.loaded && !feature.enabled
}

export interface PluginStats {
  total: number
  loaded: number
  /** 已加载、已启用且不在维护的插件。 */
  running: number
  /** 已加载且已启用但处于维护。 */
  maintenance: number
  /** 已加载但被停用。 */
  disabled: number
  unloaded: number
  protectedCount: number
}

/** 插件分段统计；running / maintenance / disabled / unloaded 互斥，加起来等于 total。 */
export function pluginStats(features: FeatureInfo[]): PluginStats {
  const loaded = features.filter((item) => item.loaded)
  return {
    total: features.length,
    loaded: loaded.length,
    running: loaded.filter((item) => item.enabled && !item.maintenance).length,
    maintenance: loaded.filter((item) => item.enabled && item.maintenance).length,
    disabled: loaded.filter((item) => !item.enabled).length,
    unloaded: features.length - loaded.length,
    protectedCount: features.filter((item) => item.protected).length,
  }
}

/** 第三方插件默认未加载，启用开关必须在未加载时也可用（重启后才生效）。 */
export function toggleLocked(feature: FeatureInfo): boolean {
  return feature.protected || (!feature.loaded && feature.source !== 'external')
}

export function maintenanceLocked(feature: FeatureInfo): boolean {
  return feature.protected || !feature.loaded
}
