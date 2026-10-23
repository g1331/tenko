<script setup lang="ts">
import { Lock } from '@element-plus/icons-vue'
import type { FeatureInfo } from '@/api'
import ExternalTag from '@/components/ExternalTag.vue'
import StatusTag from '@/components/StatusTag.vue'

// 插件本身的运行状态（未加载 / 已在插件页停用）、扩展插件来源和受控标记。
defineProps<{ feature: FeatureInfo }>()
</script>

<template>
  <ExternalTag v-if="feature.source === 'external'" :local="feature.local" />
  <StatusTag v-if="feature.controlled" kind="pending" title="受控插件：群里需 BOT 管理员开启"
    ><el-icon class="tag-icon"><Lock /></el-icon>受控</StatusTag
  >
  <StatusTag v-if="!feature.loaded" kind="pending">未加载</StatusTag>
  <StatusTag v-else-if="!feature.enabled" kind="off">插件已停用</StatusTag>
</template>
