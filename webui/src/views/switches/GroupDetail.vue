<script setup lang="ts">
import { computed } from 'vue'
import { Lock } from '@element-plus/icons-vue'
import PagePanel from '@/components/PagePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import FeaturePills from './FeaturePills.vue'
import StateText from './StateText.vue'
import SwitchItem from './SwitchItem.vue'
import {
  featureLabel,
  globalChip,
  groupLabel,
  groupStat,
  isOn,
  type GroupRef,
  type SwitchModel,
} from './model'
import { useSwitchActions } from './useSwitchActions'

const props = defineProps<{ group: GroupRef; model: SwitchModel }>()
const emit = defineEmits<{ openPlugin: [plugin: string] }>()

const { isPending, locked, setGroup } = useSwitchActions()
const stat = computed(() => groupStat(props.model, props.group.id))

/** 该群里一个群级功能的控件类型，以及不能直接修改时的原因。 */
function describe(feature: SwitchModel['features'][number]) {
  if (feature.protected) return { kind: 'protected' as const, note: '' }
  if (feature.maintenance)
    return { kind: 'maintenance' as const, note: '插件维护中，群开关暂不可改' }
  if (feature.global_enabled != null)
    return { kind: 'global' as const, note: '已被全局策略覆盖，请在按插件查看中恢复群级开关' }
  const note = !feature.loaded
    ? '插件未加载；开关状态会保留，加载后生效'
    : !feature.enabled
      ? '插件已在插件页停用；开关状态会保留，启用后生效'
      : ''
  return { kind: 'switch' as const, note }
}
</script>

<template>
  <PagePanel class="sw-panel" accent="coral">
    <div class="sw-head">
      <div>
        <div class="sw-kicker">GROUP</div>
        <h2>{{ groupLabel(group) }}</h2>
        <div class="sw-id">
          <code v-if="group.name">{{ group.id }}</code>
          <span class="sw-count">{{ model.groupFeatures.length }} 个群级功能</span>
        </div>
      </div>
      <div class="sw-summary">
        <b>{{ stat.on }}</b> / {{ stat.total }}<br />可调功能已开启
      </div>
    </div>
    <ul v-if="model.groupFeatures.length" class="sw-items">
      <SwitchItem
        v-for="feature in model.groupFeatures"
        :key="feature.plugin"
        :name="featureLabel(feature)"
        :code="feature.plugin"
        :note="describe(feature).note"
        :dim="!feature.loaded || !feature.enabled"
      >
        <template #pills><FeaturePills :feature="feature" /></template>
        <template v-if="describe(feature).kind === 'protected'">
          <StatusTag kind="prot"
            ><el-icon class="tag-icon"><Lock /></el-icon>受保护</StatusTag
          >
        </template>
        <template v-else-if="describe(feature).kind === 'maintenance'">
          <StatusTag kind="pending">维护中</StatusTag>
          <el-switch
            :model-value="false"
            disabled
            :aria-label="`${featureLabel(feature)} / ${groupLabel(group)}：维护中，不可修改`"
          />
        </template>
        <template v-else-if="describe(feature).kind === 'global'">
          <StatusTag :kind="globalChip(feature).kind">{{ globalChip(feature).text }}</StatusTag>
        </template>
        <template v-else>
          <StateText :on="isOn(feature, group.id)" />
          <el-switch
            :model-value="isOn(feature, group.id)"
            :loading="isPending(feature.plugin, group.id)"
            :disabled="locked()"
            :aria-label="`${featureLabel(feature)} / ${groupLabel(group)}`"
            @change="(value: unknown) => setGroup(feature, group, value === true)"
          />
        </template>
      </SwitchItem>
    </ul>
    <p v-else class="sw-note">暂无群级功能。</p>
  </PagePanel>

  <PagePanel
    v-if="model.globalFeatures.length"
    class="sw-panel"
    accent="gold"
    title="全局功能（对所有群生效）"
  >
    <ul class="sw-items flush">
      <SwitchItem
        v-for="feature in model.globalFeatures"
        :key="feature.plugin"
        :name="featureLabel(feature)"
        :code="feature.plugin"
      >
        <StatusTag :kind="globalChip(feature).kind">{{ globalChip(feature).text }}</StatusTag>
        <StatusTag v-if="feature.protected" kind="prot">受保护</StatusTag>
        <StatusTag v-else-if="feature.maintenance" kind="pending">维护中</StatusTag>
        <FeaturePills :feature="feature" />
        <a
          class="tk-link"
          :href="`#/switches?view=plugin&id=${encodeURIComponent(feature.plugin)}`"
          @click.prevent="emit('openPlugin', feature.plugin)"
          >在按插件查看中修改</a
        >
      </SwitchItem>
    </ul>
  </PagePanel>
</template>
