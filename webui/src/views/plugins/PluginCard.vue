<script setup lang="ts">
import { computed } from 'vue'
import type { FeatureInfo } from '@/api'
import ExternalTag from '@/components/ExternalTag.vue'
import StatusTag from '@/components/StatusTag.vue'
import { featureStatus } from '@/composables/status'

// 单个插件卡片：状态、范围与可执行的插件级操作；具体请求由页面处理。
const props = defineProps<{ feature: FeatureInfo; busy: boolean }>()
const emit = defineEmits<{
  toggle: []
  maintenance: []
  globalToggle: []
  resetGlobal: []
}>()

const status = computed(() => featureStatus(props.feature))
const groups = computed(() => Object.values(props.feature.groups ?? {}))
const enabledGroups = computed(() => groups.value.filter(Boolean).length)
const isGlobal = computed(() => props.feature.scope === 'global')
const scopeText = computed(() => {
  if (isGlobal.value) return '全局功能 · 统一策略'
  return groups.value.length
    ? `群级功能 · ${enabledGroups.value}/${groups.value.length} 个群启用`
    : '群级功能 · 暂无群组'
})
const locked = computed(() => props.feature.protected || !props.feature.loaded)
// 第三方插件默认未加载，启用开关必须在未加载时也可用（重启后才生效）。
const toggleLocked = computed(
  () => props.feature.protected || (!props.feature.loaded && props.feature.source !== 'external'),
)
const accent = computed(() =>
  !props.feature.loaded || !props.feature.enabled
    ? 'off'
    : props.feature.maintenance
      ? 'maint'
      : props.feature.protected
        ? 'prot'
        : 'run',
)
</script>

<template>
  <article class="plugin-card" :class="`is-${accent}`" :data-plugin="feature.plugin">
    <header>
      <div class="title">
        <strong class="name">{{ feature.name || feature.plugin }}</strong>
        <code class="key">{{ feature.plugin }}</code>
      </div>
      <StatusTag :kind="status.kind">{{ status.label }}</StatusTag>
    </header>
    <p class="desc">{{ scopeText }}</p>
    <div class="flags">
      <el-tag
        size="small"
        effect="plain"
        :type="feature.loaded ? 'info' : 'warning'"
        disable-transitions
      >
        {{ feature.loaded ? '已加载' : '未加载' }}
      </el-tag>
      <el-tag size="small" effect="plain" type="info" disable-transitions>
        {{ feature.enabled ? '已启用' : '已停用' }}
      </el-tag>
      <el-tag
        v-if="feature.maintenance"
        size="small"
        effect="plain"
        type="warning"
        disable-transitions
      >
        维护中
      </el-tag>
      <ExternalTag v-if="feature.source === 'external'" />
      <el-tag v-if="feature.protected" size="small" effect="plain" disable-transitions
        >受保护</el-tag
      >
      <el-tag size="small" effect="plain" type="info" disable-transitions>
        范围 {{ isGlobal ? '全局' : feature.scope === 'group' ? '群级' : feature.scope }}
      </el-tag>
    </div>
    <div class="actions">
      <el-button size="small" :disabled="toggleLocked || busy" @click="emit('toggle')">
        {{ feature.enabled ? '停用插件' : '启用插件' }}
      </el-button>
      <el-button
        v-if="isGlobal"
        size="small"
        :disabled="feature.protected || busy"
        @click="emit('globalToggle')"
      >
        {{ feature.global_enabled === false ? '开启全局功能' : '关闭全局功能' }}
      </el-button>
      <el-button
        v-else-if="feature.global_enabled != null"
        size="small"
        :disabled="feature.protected || busy"
        @click="emit('resetGlobal')"
      >
        恢复群级开关
      </el-button>
      <el-button size="small" :disabled="locked || busy" @click="emit('maintenance')">
        {{ feature.maintenance ? '解除维护' : '进入维护' }}
      </el-button>
    </div>
  </article>
</template>

<style scoped>
.plugin-card {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 10px;
  padding: 16px 18px;
  border-top: 3px solid var(--tk-ok-solid);
  border-radius: 4px;
  background: var(--tk-surface);
  box-shadow: var(--tk-shadow);
}
.plugin-card.is-maint {
  border-top-color: var(--tk-gold);
}
.plugin-card.is-off {
  border-top-color: var(--tk-switch-off);
}
.plugin-card.is-prot {
  border-top-color: var(--tk-rule);
}
header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
.title {
  min-width: 0;
}
.name {
  display: block;
  color: var(--tk-heading);
  font-size: 15px;
  overflow-wrap: anywhere;
}
.key {
  display: block;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.desc {
  margin: 0;
  color: var(--tk-text);
  font-size: 13px;
}
.flags {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: auto;
  padding-top: 12px;
  border-top: 1px dashed var(--tk-line-strong);
}
.actions .el-button {
  margin: 0;
}
</style>
