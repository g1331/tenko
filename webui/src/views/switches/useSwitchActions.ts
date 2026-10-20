import { onScopeDispose, readonly, ref } from 'vue'
import { api, ApiError, type FeatureInfo, type FeatureToggleBody } from '@/api'
import { confirmAction } from '@/composables/confirm'
import { usePanelData } from '@/composables/usePanelData'
import { featureLabel, groupLabel, type GroupRef } from './model'

/** 批量操作的进度；页面里同一时刻只允许一个写操作批次。 */
export interface BulkProgress {
  plugin: string
  enable: boolean
  done: number
  total: number
}

const pending = ref(new Set<string>())
const progress = ref<BulkProgress | null>(null)

const errorText = (reason: unknown) =>
  reason instanceof ApiError ? reason.message : String((reason as Error)?.message ?? reason)

/**
 * 功能开关的全部写操作：成功后 refresh() 取回真实状态，失败用 toast 报告。
 * pending 里是正在写入的键（开关用 isPending 显示 loading）；批量时整体上锁。
 */
export function useSwitchActions() {
  const { refresh } = usePanelData()
  let disposed = false
  onScopeDispose(() => (disposed = true))

  const keyOf = (plugin: string, scope: string) => `${plugin}:${scope}`
  const isPending = (plugin: string, scope: string) => pending.value.has(keyOf(plugin, scope))
  const locked = () => progress.value !== null

  async function write(feature: FeatureInfo, scope: string, body: FeatureToggleBody, done: string) {
    const key = keyOf(feature.plugin, scope)
    if (pending.value.has(key)) return
    pending.value = new Set(pending.value).add(key)
    try {
      await api.manage.toggleFeature(feature.plugin, body)
      await refresh()
      ElMessage.success(done)
    } catch (reason) {
      ElMessage.error(`修改失败：${errorText(reason)}`)
    } finally {
      const next = new Set(pending.value)
      next.delete(key)
      pending.value = next
    }
  }

  const setGroup = (feature: FeatureInfo, group: GroupRef, enabled: boolean) =>
    write(
      feature,
      group.id,
      { group_id: group.id, enabled },
      `已${enabled ? '开启' : '关闭'} ${featureLabel(feature)} / ${groupLabel(group)}`,
    )

  async function setMaintenance(feature: FeatureInfo, maintenance: boolean) {
    const name = featureLabel(feature)
    const ok = await confirmAction(
      maintenance
        ? `${name} 将进入维护模式，所有群暂停响应该功能。`
        : `${name} 将解除维护，恢复各群原有设置。`,
      maintenance ? '进入维护' : '解除维护',
    )
    if (ok)
      await write(
        feature,
        'maintenance',
        { maintenance },
        `${name} ${maintenance ? '已进入维护' : '已解除维护'}`,
      )
  }

  /** enabled 为 null 表示清除全局覆盖（全局功能为“跟随默认”，群级功能为恢复逐群开关）。 */
  async function setGlobal(feature: FeatureInfo, enabled: boolean | null) {
    const name = featureLabel(feature)
    const text = enabled === null ? '恢复为跟随默认' : enabled ? '全局开启' : '全局关闭'
    if (!(await confirmAction(`${name}：${text}？`, '修改全局状态'))) return
    await write(feature, 'global', { group_id: null, enabled }, `${name} 已${text}`)
  }

  /** 按顺序逐群写入，显示 n/N 进度，结束后汇总失败并刷新。 */
  async function bulk(feature: FeatureInfo, targets: GroupRef[], enable: boolean, visible: number) {
    if (!targets.length || locked()) return
    const name = featureLabel(feature)
    const verb = enable ? '开启' : '关闭'
    const ok = await confirmAction(
      `将在 ${targets.length} 个群中${verb} ${name}（当前筛选共 ${visible} 个群）。继续？`,
      `批量${verb}`,
    )
    if (!ok) return
    const base = { plugin: feature.plugin, enable, total: targets.length }
    progress.value = { ...base, done: 0 }
    const failures: string[] = []
    let done = 0
    try {
      for (const group of targets) {
        if (disposed) return
        try {
          await api.manage.toggleFeature(feature.plugin, { group_id: group.id, enabled: enable })
        } catch (reason) {
          // 会话失效时后面的请求只会重复失败，直接中止。
          if (reason instanceof ApiError && reason.status === 401) return
          failures.push(`${groupLabel(group)}：${errorText(reason)}`)
        }
        done += 1
        progress.value = { ...base, done }
      }
    } finally {
      progress.value = null
    }
    const succeeded = targets.length - failures.length
    if (failures.length) {
      ElMessage.warning({
        message: `已完成 ${succeeded}/${targets.length}，${failures.length} 个失败：${failures.slice(0, 5).join('；')}${failures.length > 5 ? '…' : ''}`,
        duration: 8000,
        showClose: true,
      })
    } else {
      ElMessage.success(`已${verb} ${succeeded} 个群的 ${name}`)
    }
    await refresh()
  }

  return {
    progress: readonly(progress),
    isPending,
    locked,
    setGroup,
    setMaintenance,
    setGlobal,
    bulk,
  }
}
