<script setup lang="ts">
import { computed, nextTick } from 'vue'
import PageContainer from '@/components/PageContainer.vue'
import ResourceBoundary from '@/components/ResourceBoundary.vue'
import StatusTag from '@/components/StatusTag.vue'
import { featureStatus, isSwitchable } from '@/composables/status'
import { usePanelData } from '@/composables/usePanelData'
import GroupDetail from './switches/GroupDetail.vue'
import PluginDetail from './switches/PluginDetail.vue'
import SwitchList from './switches/SwitchList.vue'
import {
  buildModel,
  featureLabel,
  globalChip,
  groupLabel,
  groupStat,
  matches,
  pickSelected,
  type SwitchView,
} from './switches/model'
import { useSwitchState } from './switches/useSwitchState'
import './switches/detail.css'

const { features } = usePanelData()
const { state, setView, pick, openPlugin } = useSwitchState()

const model = computed(() => (features.data.value ? buildModel(features.data.value) : null))

const viewOptions = [
  { label: '按群查看', value: 'group' },
  { label: '按插件查看', value: 'plugin' },
]

const selectedGroup = computed(() =>
  model.value ? pickSelected(model.value.groups, state.id, (item) => item.id) : null,
)
const selectedFeature = computed(() =>
  model.value ? pickSelected(model.value.features, state.id, (item) => item.plugin) : null,
)
const groupRows = computed(() =>
  (model.value?.groups ?? []).filter((item) => matches(state.q, item.id, item.name)),
)
const pluginRows = computed(() =>
  (model.value?.features ?? []).filter((item) => matches(state.q, item.plugin, item.name)),
)

function count(total: number, matched: number, noun: string) {
  return `共 ${total} 个${noun}，匹配 ${matched} 个`
}

/** 窄屏下列表在详情上方：选中后把详情滚到可见位置。 */
async function pickAndReveal(id: string) {
  pick(id)
  if (!matchMedia('(max-width: 700px)').matches) return
  await nextTick()
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
  document.getElementById('sw-detail')?.scrollIntoView({
    behavior: reduced ? 'auto' : 'smooth',
    block: 'start',
  })
}
</script>

<template>
  <PageContainer>
    <ResourceBoundary :resource="features" label="开关数据" :rows="6">
      <el-empty v-if="model && !model.features.length" description="暂无功能开关" />
      <template v-else-if="model">
        <div class="sw-bar">
          <el-segmented
            :model-value="state.view"
            :options="viewOptions"
            aria-label="查看方式"
            @change="(value: string | number | boolean) => setView(value as SwitchView)"
          />
          <span class="sw-total"
            >{{ model.groups.length }} 个群 · {{ model.features.length }} 个插件</span
          >
        </div>

        <div class="sw-layout">
          <SwitchList
            v-if="state.view === 'group'"
            v-model="state.q"
            :items="groupRows"
            :item-key="(item) => item.id"
            :selected="selectedGroup?.id ?? ''"
            label="群"
            placeholder="搜索群名称或群号"
            :count="count(model.groups.length, groupRows.length, '群')"
            :empty="model.groups.length ? '没有匹配的群' : '暂无群组'"
            @pick="pickAndReveal"
          >
            <template #row="{ item }">
              <span class="row-main">
                <span class="row-name">{{ groupLabel(item) }}</span>
                <span v-if="item.name" class="row-sub tk-mono">{{ item.id }}</span>
              </span>
              <span class="row-side">
                <template v-if="groupStat(model, item.id).total">
                  <StatusTag v-if="!groupStat(model, item.id).on" kind="pending"
                    >全部关闭</StatusTag
                  >
                  已开 {{ groupStat(model, item.id).on }} / {{ groupStat(model, item.id).total }}
                </template>
                <template v-else>无可调功能</template>
              </span>
            </template>
          </SwitchList>
          <SwitchList
            v-else
            v-model="state.q"
            :items="pluginRows"
            :item-key="(item) => item.plugin"
            :selected="selectedFeature?.plugin ?? ''"
            label="插件"
            placeholder="搜索插件名称或 ID"
            :count="count(model.features.length, pluginRows.length, '插件')"
            empty="没有匹配的插件"
            @pick="pickAndReveal"
          >
            <template #row="{ item }">
              <span class="row-main">
                <span class="row-name">{{ featureLabel(item) }}</span>
                <span class="row-sub tk-mono"
                  >{{ item.plugin }} · {{ item.scope === 'global' ? '全局' : '群级' }}</span
                >
              </span>
              <span class="row-side">
                <template v-if="item.scope === 'global' || isSwitchable(item)">
                  <StatusTag v-if="featureStatus(item).kind === 'pending'" kind="pending">{{
                    featureStatus(item).label
                  }}</StatusTag>
                  <StatusTag v-if="item.scope === 'global'" :kind="globalChip(item).kind">{{
                    globalChip(item).text
                  }}</StatusTag>
                  <template v-else
                    >{{ Object.values(item.groups).filter(Boolean).length }} /
                    {{ Object.keys(item.groups).length }} 群</template
                  >
                </template>
                <StatusTag v-else :kind="featureStatus(item).kind">{{
                  featureStatus(item).label
                }}</StatusTag>
              </span>
            </template>
          </SwitchList>

          <div id="sw-detail" class="sw-detail">
            <template v-if="state.view === 'group'">
              <GroupDetail
                v-if="selectedGroup"
                :group="selectedGroup"
                :model="model"
                @open-plugin="openPlugin"
              />
              <el-empty v-else description="暂无群组，接口尚未返回群级开关列。" :image-size="64" />
            </template>
            <PluginDetail
              v-else-if="selectedFeature"
              :feature="selectedFeature"
              :model="model"
              :state="state"
              @update:gq="(value: string) => (state.gq = value)"
              @update:f="(value) => (state.f = value)"
            />
          </div>
        </div>
      </template>
    </ResourceBoundary>
  </PageContainer>
</template>

<style scoped>
.sw-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px 16px;
  margin-bottom: 14px;
}
.sw-total {
  color: var(--tk-muted);
  font-size: 12px;
  letter-spacing: 1px;
}
.sw-layout {
  display: grid;
  grid-template-columns: minmax(250px, 320px) minmax(0, 1fr);
  align-items: start;
  gap: 16px;
}
.sw-detail {
  display: grid;
  min-width: 0;
  gap: 16px;
  scroll-margin-top: 12px;
}
.row-main {
  display: grid;
  min-width: 0;
  gap: 2px;
}
.row-name {
  overflow: hidden;
  color: var(--tk-heading);
  font-size: 13px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row-sub {
  overflow: hidden;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row-side {
  display: flex;
  flex: none;
  align-items: center;
  gap: 7px;
  color: var(--tk-muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

@media (max-width: 700px) {
  .sw-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
