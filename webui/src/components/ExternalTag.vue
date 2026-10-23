<script setup lang="ts">
// 扩展插件（tenko.plugins 入口或本地插件目录）的来源标记；插件页与开关页共用。
defineProps<{ local?: boolean }>()
</script>

<template>
  <el-tag size="small" effect="plain" type="warning" disable-transitions>{{
    local ? '本地' : '扩展'
  }}</el-tag>
</template>
