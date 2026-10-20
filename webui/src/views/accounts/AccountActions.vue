<script setup lang="ts">
import type { AccountAction, ManagedAccount } from '@/api'

defineProps<{ account: ManagedAccount; busy: boolean }>()
const emit = defineEmits<{
  preferences: []
  action: [action: Exclude<AccountAction, 'preferences'>]
}>()
</script>

<template>
  <div v-if="account.manageable" class="actions">
    <el-button size="small" :disabled="busy" @click="emit('preferences')">偏好</el-button>
    <el-button
      v-if="account.enabled"
      size="small"
      type="danger"
      plain
      :disabled="busy"
      @click="emit('action', 'disable')"
    >
      停用
    </el-button>
    <el-button v-else size="small" :disabled="busy" @click="emit('action', 'enable')">
      启用
    </el-button>
    <el-button size="small" :disabled="!account.connected || busy" @click="emit('action', 'kick')">
      踢下线
    </el-button>
    <el-button
      size="small"
      type="danger"
      plain
      :disabled="account.connected || account.online || busy"
      @click="emit('action', 'forget')"
    >
      移除记录
    </el-button>
  </div>
  <span v-else class="hint">适配器暂不支持管理</span>
</template>

<style scoped>
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.actions .el-button {
  margin: 0;
}
.hint {
  color: var(--tk-muted);
  font-size: 12px;
}
</style>
