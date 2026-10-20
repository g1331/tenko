<script setup lang="ts">
import type { FeatureInfo } from '@/api'
import StatusTag from '@/components/StatusTag.vue'

// 插件本身的运行状态：未加载 / 已在插件页停用。
defineProps<{ feature: FeatureInfo }>()
</script>

<template>
  <StatusTag v-if="!feature.loaded" kind="pending">未加载</StatusTag>
  <StatusTag v-else-if="!feature.enabled" kind="off">插件已停用</StatusTag>
</template>
