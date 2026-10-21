<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, type FeatureInfo, type PluginToggleResult } from '@/api'
import PageContainer from '@/components/PageContainer.vue'
import ResourceBoundary from '@/components/ResourceBoundary.vue'
import { confirmAction } from '@/composables/confirm'
import { pluginStats } from '@/composables/status'
import { usePanelData } from '@/composables/usePanelData'
import { providePluginOps } from '@/composables/usePluginOps'
import InstalledTab from './plugins/InstalledTab.vue'
import MarketTab from './plugins/MarketTab.vue'
import PluginBanners from './plugins/PluginBanners.vue'
import PluginCard from './plugins/PluginCard.vue'

const { features, refresh } = usePanelData()

const ops = providePluginOps()
const tab = ref('all')
const query = ref('')
const busy = ref('')

// 页面级的重启提醒和任务进度依赖已安装插件数据，进页面就读取；市场目录等切到标签页再读。
onMounted(() => ops.loadInstalled())
function onTab(name: string | number) {
  if (name === 'market') void ops.ensureMarket()
  if (name === 'installed') void ops.loadInstalled()
}

const filtered = (list: FeatureInfo[]) => {
  const text = query.value.trim().toLowerCase()
  return text
    ? list.filter((item) => `${item.name} ${item.plugin}`.toLowerCase().includes(text))
    : list
}

const summary = computed(() => {
  const list = features.data.value?.features
  if (!list) return ''
  const stats = pluginStats(list)
  return `共 ${stats.total} 个插件 · ${stats.running} 个运行中`
})

type Change = { title: string; message: string; done: string; body: () => Promise<unknown> }

/** 确认 → 提交 → 成功提示并刷新共享数据；失败只提示，不改变界面状态。 */
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
    if (result?.restart_required) ElMessage.info('已启用，重启后生效')
    else ElMessage.success(change.done)
    await Promise.all([refresh(), ops.loadInstalled()])
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
</script>

<template>
  <PageContainer>
    <PluginBanners />
    <el-tabs v-model="tab" @tab-change="onTab">
      <el-tab-pane label="全部插件" name="all">
        <div class="toolbar">
          <el-input
            v-model="query"
            class="search"
            clearable
            placeholder="搜索插件名称或标识"
            aria-label="搜索插件"
          />
          <span class="summary">{{ summary }}</span>
        </div>

        <ResourceBoundary :resource="features" label="插件数据">
          <template #default="{ data }">
            <el-empty v-if="!data.features.length" :image-size="64" description="暂无插件" />
            <el-empty
              v-else-if="!filtered(data.features).length"
              :image-size="64"
              description="没有匹配的插件"
            />
            <div v-else class="grid">
              <PluginCard
                v-for="feature in filtered(data.features)"
                :key="feature.plugin"
                :feature="feature"
                :busy="busy === feature.plugin"
                @toggle="toggle(feature)"
                @maintenance="maintenance(feature)"
                @global-toggle="globalToggle(feature)"
                @reset-global="resetGlobal(feature)"
              />
            </div>
          </template>
        </ResourceBoundary>
      </el-tab-pane>
      <el-tab-pane label="插件市场" name="market" lazy>
        <MarketTab />
      </el-tab-pane>
      <el-tab-pane label="第三方插件" name="installed" lazy>
        <InstalledTab />
      </el-tab-pane>
    </el-tabs>
  </PageContainer>
</template>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  margin-bottom: 16px;
}
.search {
  width: 260px;
  max-width: 100%;
}
.summary {
  color: var(--tk-muted);
  font-size: 12px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 300px), 1fr));
  gap: 16px;
}
@media (max-width: 520px) {
  .search {
    width: 100%;
  }
  .grid {
    gap: 12px;
  }
}
</style>
