import { ref } from 'vue'
import { api, type FeatureInfo, type PluginToggleResult } from '@/api'
import { confirmAction } from '@/composables/confirm'

type Change = { title: string; message: string; done: string; body: () => Promise<unknown> }

/**
 * 插件级操作：启停、维护、全局开关、受控状态。确认 → 提交 → 提示并调用 after 刷新数据；
 * 失败只提示，不改变界面状态。插件列表和插件详情页共用。
 */
/** 启用后仍需重启时的提示，带上没能直接加载的原因。 */
export const restartNotice = (reason?: string | null) =>
  reason ? `已启用，${reason}，重启后生效` : '已启用，重启后生效'

export function usePluginActions(after: () => Promise<unknown>) {
  const busy = ref('')

  async function apply(feature: FeatureInfo, change: Change) {
    const name = feature.name || feature.plugin
    const ok = await confirmAction(
      `${change.message}插件：${name}（${feature.plugin}）。`,
      change.title,
    )
    if (!ok) return
    busy.value = feature.plugin
    try {
      const result = (await change.body()) as Partial<PluginToggleResult> | undefined
      if (result?.restart_required) ElMessage.info(restartNotice(result.restart_reason))
      else ElMessage.success(change.done)
      await after()
    } catch (error) {
      ElMessage.error(`${change.title}失败：${(error as Error).message}`)
    } finally {
      busy.value = ''
    }
  }

  const toggle = (feature: FeatureInfo) => {
    const enabled = !feature.enabled
    return apply(feature, {
      title: enabled ? '启用插件' : '停用插件',
      message: enabled ? '插件将恢复响应。' : '插件将停止在所有群响应。',
      done: enabled ? '插件已启用' : '插件已停用',
      body: () => api.manage.togglePlugin(feature.plugin, { enabled, confirm: true }),
    })
  }
  const maintenance = (feature: FeatureInfo) => {
    const next = !feature.maintenance
    return apply(feature, {
      title: next ? '进入维护' : '解除维护',
      message: next ? '维护期间所有群的该功能暂停响应。' : '解除后恢复各群原有设置。',
      done: next ? '已进入维护' : '已解除维护',
      body: () => api.manage.toggleFeature(feature.plugin, { maintenance: next }),
    })
  }
  const globalToggle = (feature: FeatureInfo) => {
    const enabled = feature.global_enabled === false
    return apply(feature, {
      title: enabled ? '开启全局功能' : '关闭全局功能',
      message: enabled ? '全局功能将对所有群开启。' : '全局功能将对所有群关闭。',
      done: enabled ? '全局功能已开启' : '全局功能已关闭',
      body: () => api.manage.toggleFeature(feature.plugin, { group_id: null, enabled }),
    })
  }
  const resetGlobal = (feature: FeatureInfo) =>
    apply(feature, {
      title: '恢复群级开关',
      message: '清除全局覆盖，各群恢复原有的逐群开关。',
      done: '已恢复群级开关',
      body: () => api.manage.toggleFeature(feature.plugin, { group_id: null, enabled: null }),
    })

  /** 改回插件自己声明的值时清除覆盖（null），面板不保留与声明相同的覆盖。 */
  const controlled = (feature: FeatureInfo) => {
    const next = !feature.controlled
    return apply(feature, {
      title: next ? '设为受控插件' : '取消受控',
      message: next
        ? '未单独开启过的群将关闭该插件，群里只有 BOT 管理员能开启，私聊仅 BOT 管理员可用。'
        : '该插件恢复为普通插件，群管理可以自行开关。',
      done: next ? '已设为受控插件' : '已取消受控',
      body: () =>
        api.manage.toggleFeature(feature.plugin, {
          controlled: next === feature.controlled_declared ? null : next,
        }),
    })
  }

  return { busy, toggle, maintenance, globalToggle, resetGlobal, controlled }
}
