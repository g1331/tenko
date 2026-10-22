<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, ApiError } from '@/api'
import type { NotifySettings } from '@/api/types'
import PagePanel from '@/components/PagePanel.vue'
import ResourceBoundary from '@/components/ResourceBoundary.vue'
import { usePanelData } from '@/composables/usePanelData'

const { settings, managedAccounts, refresh } = usePanelData()

// 账号 ID 都是数字，不会与这个哨兵值冲突；空字符串会被下拉框当成未选择。
const AUTO = 'auto'

// 只保存用户改动的值：轮询刷新不会覆盖未保存的编辑。
const draft = ref<Partial<NotifySettings>>({})
const saving = ref(false)

const saved = computed(() => settings.data.value?.notify)
const current = computed<NotifySettings>(() => ({
  mode: 'private',
  group: null,
  account: null,
  ...saved.value,
  ...draft.value,
}))
const dirty = computed(() =>
  (Object.keys(draft.value) as (keyof NotifySettings)[]).some(
    (key) => draft.value[key] !== saved.value?.[key],
  ),
)
const accounts = computed(
  () => managedAccounts.data.value?.accounts.filter((item) => item.manageable) ?? [],
)

function edit<K extends keyof NotifySettings>(key: K, value: NotifySettings[K]) {
  draft.value = { ...draft.value, [key]: value }
}

async function save() {
  saving.value = true
  try {
    await api.manage.saveSettings({ notify: draft.value })
    draft.value = {}
    ElMessage.success('通知设置已保存')
    await refresh()
  } catch (error) {
    ElMessage.error(
      `保存通知设置失败：${error instanceof ApiError ? error.message : String(error)}`,
    )
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <PagePanel title="通知">
    <ResourceBoundary :resource="settings" label="通知设置" :rows="3">
      <p class="desc">
        启动通知、异常报告、好友申请、入群邀请、插件更新和账号掉线提醒都按这里发送。保存后立即生效；也可由
        Master 私聊发送 /通知设置 修改，/通知设置 测试 可以发一条测试通知。
      </p>
      <div class="row">
        <label class="label" id="notify-mode-label">发往</label>
        <el-radio-group
          :model-value="current.mode"
          aria-labelledby="notify-mode-label"
          @update:model-value="edit('mode', $event as NotifySettings['mode'])"
        >
          <el-radio-button value="private">私聊 Master</el-radio-button>
          <el-radio-button value="group">通知群</el-radio-button>
        </el-radio-group>
        <el-input
          v-if="current.mode === 'group'"
          :model-value="current.group ?? ''"
          class="input"
          placeholder="群号"
          aria-label="通知群号"
          @update:model-value="edit('group', $event.trim() || null)"
        />
      </div>
      <div class="row">
        <label class="label" id="notify-account-label">发送账号</label>
        <el-select
          :model-value="current.account ?? AUTO"
          class="input"
          aria-labelledby="notify-account-label"
          @update:model-value="edit('account', $event === AUTO ? null : $event)"
        >
          <el-option label="自动（任选在线账号）" :value="AUTO" />
          <el-option
            v-for="item in accounts"
            :key="item.id"
            :label="`${item.alias || item.name}（${item.id}）${item.online ? '' : ' · 离线'}`"
            :value="item.id"
          />
        </el-select>
        <el-button type="primary" :disabled="!dirty" :loading="saving" @click="save">
          保存通知设置
        </el-button>
      </div>
      <p class="desc">
        私聊时 Master
        需要是发送账号的好友；发往通知群时，发送账号需要在群里。指定的账号不在线时，改用其他在线账号发送。
      </p>
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
.input {
  width: 240px;
}
@media (max-width: 640px) {
  .input {
    flex: 1 1 160px;
    width: auto;
  }
}
</style>
