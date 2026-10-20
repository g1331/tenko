<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, ApiError } from '@/api'
import PagePanel from '@/components/PagePanel.vue'
import ResourceBoundary from '@/components/ResourceBoundary.vue'
import { usePanelData } from '@/composables/usePanelData'

const { settings, refresh } = usePanelData()

// 只保存用户改动的值：轮询刷新不会覆盖未保存的选择。
const draft = ref<boolean | null>(null)
const saving = ref(false)

const current = computed(() => draft.value ?? settings.data.value?.default_enabled ?? true)
const dirty = computed(
  () => draft.value !== null && draft.value !== settings.data.value?.default_enabled,
)

async function save() {
  saving.value = true
  try {
    await api.manage.saveSettings({ default_enabled: current.value })
    draft.value = null
    ElMessage.success('运行设置已保存')
    await refresh()
  } catch (error) {
    ElMessage.error(
      `保存运行设置失败：${error instanceof ApiError ? error.message : String(error)}`,
    )
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <PagePanel title="功能默认策略">
    <ResourceBoundary :resource="settings" label="运行设置" :rows="2">
      <template #default="{ data }">
        <p class="desc">影响没有显式开关的群功能，已有群级或全局设置继续优先。</p>
        <div class="row">
          <label class="label" id="policy-label">未设置的功能</label>
          <el-select
            :model-value="current"
            class="select"
            aria-labelledby="policy-label"
            @update:model-value="draft = $event"
          >
            <el-option label="默认启用" :value="true" />
            <el-option label="默认关闭" :value="false" />
          </el-select>
          <el-button type="primary" :disabled="!dirty" :loading="saving" @click="save">
            保存运行设置
          </el-button>
        </div>
        <p class="hint">
          {{
            data.stored ? '已保存到数据库，重启后恢复。' : '当前沿用宿主配置；保存后由数据库管理。'
          }}
        </p>
      </template>
    </ResourceBoundary>
  </PagePanel>
</template>

<style scoped>
.desc,
.hint {
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
