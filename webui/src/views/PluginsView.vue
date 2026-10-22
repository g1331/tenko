<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { FeatureInfo } from '@/api'
import PageContainer from '@/components/PageContainer.vue'
import ResourceBoundary from '@/components/ResourceBoundary.vue'
import { pluginStats } from '@/composables/status'
import { usePanelData } from '@/composables/usePanelData'
import { usePluginActions } from '@/composables/usePluginActions'
import { providePluginOps } from '@/composables/usePluginOps'
import InstalledTab from './plugins/InstalledTab.vue'
import MarketTab from './plugins/MarketTab.vue'
import PluginBanners from './plugins/PluginBanners.vue'
import PluginCard from './plugins/PluginCard.vue'

const { features, refresh } = usePanelData()

const ops = providePluginOps()
const tab = ref('all')
const query = ref('')
const { busy, toggle, maintenance, globalToggle, resetGlobal } = usePluginActions(() =>
  Promise.all([refresh(), ops.loadInstalled()]),
)

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
      <el-tab-pane label="扩展插件" name="installed" lazy>
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
