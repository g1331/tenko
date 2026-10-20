<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, ApiError } from '@/api'
import type { FriendRequestMode } from '@/api/types'
import PagePanel from '@/components/PagePanel.vue'
import ResourceBoundary from '@/components/ResourceBoundary.vue'
import { usePanelData } from '@/composables/usePanelData'

const { settings, refresh } = usePanelData()

const MODES: { value: FriendRequestMode; label: string; hint: string }[] = [
  { value: 'off', label: '关闭', hint: '不处理好友申请，也不通知 Master。' },
  {
    value: 'admins',
    label: '仅管理员',
    hint: 'Master 与 BotAdmin 的申请自动同意，其余保持待处理并私聊通知 Master。',
  },
  { value: 'all', label: '所有人', hint: '自动同意所有好友申请。' },
]

// 只保存用户改动的值：轮询刷新不会覆盖未保存的选择。
const draft = ref<FriendRequestMode | null>(null)
const saving = ref(false)

const current = computed(() => draft.value ?? settings.data.value?.friend_request_mode ?? 'admins')
const dirty = computed(
  () => draft.value !== null && draft.value !== settings.data.value?.friend_request_mode,
)
const hint = computed(() => MODES.find((mode) => mode.value === current.value)?.hint)

async function save() {
  saving.value = true
  try {
    await api.manage.saveSettings({ friend_request_mode: current.value })
    draft.value = null
    ElMessage.success('好友申请策略已保存')
    await refresh()
  } catch (error) {
    ElMessage.error(
      `保存好友申请策略失败：${error instanceof ApiError ? error.message : String(error)}`,
    )
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <PagePanel title="好友申请">
    <ResourceBoundary :resource="settings" label="好友申请策略" :rows="2">
      <p class="desc">保存后立即生效，无需重启；也可由 Master 私聊发送 /好友申请 修改。</p>
      <div class="row">
        <label class="label" id="friend-mode-label">处理策略</label>
        <el-select
          :model-value="current"
          class="select"
          aria-labelledby="friend-mode-label"
          @update:model-value="draft = $event"
        >
          <el-option
            v-for="mode in MODES"
            :key="mode.value"
            :label="mode.label"
            :value="mode.value"
          />
        </el-select>
        <el-button type="primary" :disabled="!dirty" :loading="saving" @click="save">
          保存好友申请策略
        </el-button>
      </div>
      <p class="desc">{{ hint }}</p>
    </ResourceBoundary>
  </PagePanel>
</template>

<style scoped>
.desc {
  margin: 0;
  color: var(--tk-muted);
  font-size: 12px;
}
.row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 12px;
  margin: 14px 0 10px;
}
.label {
  color: var(--tk-heading);
  font-weight: 600;
}
.select {
  width: 180px;
}
@media (max-width: 640px) {
  .select {
    flex: 1 1 140px;
    width: auto;
  }
}
</style>
