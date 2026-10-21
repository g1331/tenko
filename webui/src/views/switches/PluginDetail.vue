<script setup lang="ts">
import { computed } from 'vue'
import { Lock, Search } from '@element-plus/icons-vue'
import PagePanel from '@/components/PagePanel.vue'
import ExternalTag from '@/components/ExternalTag.vue'
import StatusTag from '@/components/StatusTag.vue'
import { featureStatus, isSwitchable } from '@/composables/status'
import type { FeatureInfo } from '@/api'
import StateText from './StateText.vue'
import SwitchItem from './SwitchItem.vue'
import {
  featureLabel,
  globalChip,
  globalMode,
  groupLabel,
  isOn,
  visibleGroups,
  type GroupFilter,
  type SwitchModel,
  type SwitchState,
} from './model'
import { useSwitchActions } from './useSwitchActions'

const props = defineProps<{ feature: FeatureInfo; model: SwitchModel; state: SwitchState }>()
const emit = defineEmits<{ 'update:gq': [value: string]; 'update:f': [value: GroupFilter] }>()

const { progress, isPending, locked, setGroup, setMaintenance, setGlobal, bulk } =
  useSwitchActions()

const name = computed(() => featureLabel(props.feature))
const status = computed(() => featureStatus(props.feature))
const isGlobal = computed(() => props.feature.scope === 'global')
const switchable = computed(() => isSwitchable(props.feature))
const total = computed(() => Object.keys(props.feature.groups ?? {}).length)
const enabledCount = computed(
  () => Object.values(props.feature.groups ?? {}).filter(Boolean).length,
)

const visible = computed(() => visibleGroups(props.feature, props.model, props.state))
const needOn = computed(() => visible.value.filter((group) => !isOn(props.feature, group.id)))
const needOff = computed(() => visible.value.filter((group) => isOn(props.feature, group.id)))
const onCount = computed(
  () => props.model.groups.filter((group) => isOn(props.feature, group.id)).length,
)
const running = computed(() =>
  progress.value?.plugin === props.feature.plugin ? progress.value : null,
)

const maintenanceHint = computed(() =>
  props.feature.protected
    ? '受保护功能不可进入维护'
    : !props.feature.loaded
      ? '插件未加载，无法切换维护状态'
      : '维护期间所有群的该功能暂停响应',
)
const globalHint = computed(() =>
  props.feature.protected ? '受保护功能不可关闭' : '对所有群统一生效；“跟随默认”沿用默认功能策略',
)
const note = computed(() =>
  props.feature.protected
    ? '受保护功能在所有群恒定开启，无需逐群设置。'
    : props.feature.maintenance
      ? '维护期间无法逐群修改；解除维护后恢复各群原有设置。'
      : '逐群开关已被全局策略覆盖。',
)

function onGlobal(mode: string | number | boolean | undefined) {
  if (mode === globalMode(props.feature)) return
  void setGlobal(props.feature, mode === 'on' ? true : mode === 'off' ? false : null)
}
</script>

<template>
  <PagePanel class="sw-panel">
    <div class="sw-head">
      <div>
        <h2>{{ name }}</h2>
        <div class="sw-id">
          <code>{{ feature.plugin }}</code>
          <ExternalTag v-if="feature.source === 'external'" />
          <StatusTag kind="prot">{{ isGlobal ? '全局功能' : '群级功能' }}</StatusTag>
          <StatusTag :kind="status.kind">{{ status.label }}</StatusTag>
        </div>
      </div>
      <div v-if="!isGlobal" class="sw-summary">
        <b>{{ enabledCount }}</b> / {{ total }}<br />个群生效
      </div>
    </div>

    <ul class="sw-items sw-controls">
      <SwitchItem name="维护模式" :note="maintenanceHint">
        <StateText :on="feature.maintenance" on-text="维护中" off-text="正常" />
        <el-switch
          :model-value="feature.maintenance"
          :disabled="feature.protected || !feature.loaded || locked()"
          :loading="isPending(feature.plugin, 'maintenance')"
          :aria-label="`${name} 维护模式`"
          @change="(value: unknown) => setMaintenance(feature, value === true)"
        />
      </SwitchItem>

      <SwitchItem v-if="isGlobal" name="全局状态" :note="globalHint">
        <StatusTag v-if="feature.protected" :kind="globalChip(feature).kind">
          <el-icon class="tag-icon"><Lock /></el-icon>受保护 · {{ globalChip(feature).text }}
        </StatusTag>
        <el-radio-group
          v-else
          :model-value="globalMode(feature)"
          size="small"
          aria-label="全局状态"
          :disabled="locked() || isPending(feature.plugin, 'global')"
          @change="onGlobal"
        >
          <el-radio-button value="on">开启</el-radio-button>
          <el-radio-button value="off">关闭</el-radio-button>
          <el-radio-button value="default">跟随默认</el-radio-button>
        </el-radio-group>
      </SwitchItem>
      <SwitchItem
        v-else-if="feature.global_enabled != null"
        name="全局策略覆盖"
        :note="`该功能被全局策略统一设为${feature.global_enabled ? '开启' : '关闭'}，逐群开关暂不生效`"
      >
        <StatusTag :kind="globalChip(feature).kind">{{ globalChip(feature).text }}</StatusTag>
        <el-button
          size="small"
          :disabled="feature.protected || locked()"
          :loading="isPending(feature.plugin, 'global')"
          @click="setGlobal(feature, null)"
        >
          恢复群级开关
        </el-button>
      </SwitchItem>
    </ul>

    <template v-if="!isGlobal">
      <template v-if="switchable">
        <h3 class="sw-section">各群开关</h3>
        <div class="sw-tools">
          <el-input
            :model-value="state.gq"
            clearable
            aria-label="搜索群"
            placeholder="搜索群名称或群号"
            @update:model-value="(value: string) => emit('update:gq', value)"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
        </div>
        <div class="sw-bulk">
          <el-radio-group
            :model-value="state.f"
            size="small"
            aria-label="按状态筛选"
            @change="
              (value: string | number | boolean | undefined) =>
                emit('update:f', value as GroupFilter)
            "
          >
            <el-radio-button value="all">全部 {{ model.groups.length }}</el-radio-button>
            <el-radio-button value="on">已开启 {{ onCount }}</el-radio-button>
            <el-radio-button value="off"
              >已关闭 {{ model.groups.length - onCount }}</el-radio-button
            >
          </el-radio-group>
          <span class="sw-count">显示 {{ visible.length }} / {{ model.groups.length }} 个群</span>
          <div class="sw-bulk-actions">
            <el-button
              size="small"
              :disabled="!needOn.length || locked()"
              @click="bulk(feature, needOn, true, visible.length)"
            >
              全部开启{{ needOn.length ? `（${needOn.length}）` : '' }}
            </el-button>
            <el-button
              size="small"
              :disabled="!needOff.length || locked()"
              @click="bulk(feature, needOff, false, visible.length)"
            >
              全部关闭{{ needOff.length ? `（${needOff.length}）` : '' }}
            </el-button>
          </div>
        </div>
        <div v-if="running" class="sw-progress" role="status" aria-live="polite">
          <span
            >{{ running.enable ? '正在开启' : '正在关闭' }} {{ running.done }}/{{
              running.total
            }}</span
          >
          <el-progress
            :percentage="Math.round((running.done / running.total) * 100)"
            :show-text="false"
            :stroke-width="8"
            color="var(--tk-ok-solid)"
          />
        </div>
        <el-scrollbar v-if="visible.length" max-height="520px" class="sw-groups">
          <ul class="sw-items">
            <SwitchItem
              v-for="group in visible"
              :key="group.id"
              :name="groupLabel(group)"
              :code="group.name ? group.id : undefined"
            >
              <StateText :on="isOn(feature, group.id)" />
              <el-switch
                :model-value="isOn(feature, group.id)"
                :loading="isPending(feature.plugin, group.id)"
                :disabled="locked()"
                :aria-label="`${name} / ${groupLabel(group)}`"
                @change="(value: unknown) => setGroup(feature, group, value === true)"
              />
            </SwitchItem>
          </ul>
        </el-scrollbar>
        <p v-else class="sw-empty">没有符合条件的群</p>
      </template>
      <p v-else class="sw-note sw-note-top">{{ note }}</p>
    </template>
  </PagePanel>
</template>

<style scoped>
.sw-controls :deep(.sw-item:first-child) {
  border-top: 1px solid var(--tk-line);
}
.sw-section {
  margin: 0;
  padding: 14px 20px 8px;
  border-top: 1px solid var(--tk-line);
  color: var(--tk-heading);
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.04em;
}
.sw-tools {
  padding: 0 20px 10px;
}
.sw-bulk {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  padding: 0 20px 10px;
  color: var(--tk-muted);
  font-size: 12px;
}
.sw-bulk-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
.sw-bulk-actions .el-button + .el-button {
  margin-left: 0;
}
.sw-progress {
  display: grid;
  gap: 4px;
  padding: 0 20px 10px;
  color: var(--tk-heading);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.sw-groups {
  height: auto; /* el-scrollbar 默认 height:100%，在被拉伸的卡片里会撑出空白 */
  border-top: 1px solid var(--tk-line);
}
.sw-groups :deep(.sw-item:first-child) {
  border-top: 0;
}
.sw-empty {
  margin: 0;
  padding: 24px 14px;
  border-top: 1px solid var(--tk-line);
  color: var(--tk-muted);
  text-align: center;
}
.sw-note-top {
  padding-top: 14px;
}

@media (max-width: 700px) {
  .sw-section,
  .sw-tools,
  .sw-bulk,
  .sw-progress {
    padding-inline: 14px;
  }
  .sw-bulk-actions {
    margin-left: 0;
  }
  .sw-groups :deep(.el-scrollbar__wrap) {
    max-height: none !important;
  }
}
</style>
