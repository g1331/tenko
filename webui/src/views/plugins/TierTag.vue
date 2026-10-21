<script setup lang="ts">
import type { PluginTrust } from '@/api'

// 插件目录等级：官方 / 已收录；已安装插件还可能是未收录来源。
defineProps<{ tier: PluginTrust }>()
const TEXT: Record<PluginTrust, string> = { official: '官方', listed: '已收录', unlisted: '未收录' }
</script>

<template>
  <el-tag
    size="small"
    :effect="tier === 'official' ? 'light' : 'plain'"
    :type="tier === 'official' ? 'primary' : 'info'"
    disable-transitions
  >
    {{ TEXT[tier] }}
  </el-tag>
</template>
