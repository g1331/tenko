<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, ApiError } from '@/api'
import type { CoreConfigData, CoreConfigField, CoreConfigGroup } from '@/api/types'
import PagePanel from '@/components/PagePanel.vue'
import ResourceBoundary from '@/components/ResourceBoundary.vue'
import { confirmAction } from '@/composables/confirm'
import { resource, usePanelData } from '@/composables/usePanelData'
import { useRestart } from '@/composables/useRestart'

const GROUPS: [CoreConfigGroup, string][] = [
  ['basic', '基础'],
  ['upgrade', '升级'],
  ['render', '图片渲染'],
  ['ratelimit', '命令限流'],
  ['exception', '异常报告'],
  ['registry', '插件市场'],
]

const config = resource<CoreConfigData>()
const { refresh } = usePanelData()
const { restarting, failed: restartFailed, restart: requestRestart } = useRestart()

// 只保存用户改动的值（urls 以多行文本编辑）：重新读取不会覆盖未保存的编辑。
const draft = ref<Record<string, unknown>>({})
const saving = ref(false)

const lines = (text: string) =>
  text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
/** 把后端的值转成控件使用的形式。 */
const toForm = (field: CoreConfigField) =>
  field.kind === 'urls'
    ? ((field.value as string[] | null) ?? []).join('\n')
    : (field.value ?? (field.kind === 'identifier' ? '' : null))
const toBody = (field: CoreConfigField, value: unknown) =>
  field.kind === 'urls' ? lines(String(value)) : value
const same = (field: CoreConfigField, value: unknown) =>
  JSON.stringify(toBody(field, value)) === JSON.stringify(toBody(field, toForm(field)))

const current = (field: CoreConfigField) =>
  field.key in draft.value ? draft.value[field.key] : toForm(field)
function edit(field: CoreConfigField, value: unknown) {
  // 数字框被清空时给出 null，保持原值。
  if (value === null && field.kind !== 'identifier') return
  if (same(field, value)) delete draft.value[field.key]
  else draft.value[field.key] = value
}

const writable = computed(() => !!config.data.value?.writable)
const dirty = computed(() => Object.keys(draft.value).length > 0)
const groups = computed(() =>
  GROUPS.map(([group, title]) => ({
    title,
    fields: (config.data.value?.fields ?? []).filter((field) => field.group === group),
  })).filter((item) => item.fields.length),
)

async function load() {
  try {
    config.data.value = await api.manage.config()
    config.error.value = null
  } catch (error) {
    config.error.value = error instanceof ApiError ? error : new ApiError(String(error))
  }
  config.loading.value = false
}
onMounted(load)

async function save() {
  const fields = config.data.value?.fields ?? []
  const values = Object.fromEntries(
    fields.filter((f) => f.key in draft.value).map((f) => [f.key, toBody(f, draft.value[f.key])]),
  )
  saving.value = true
  try {
    config.data.value = await api.manage.saveConfig(values)
    draft.value = {}
    ElMessage.success('宿主配置已保存，重启后生效')
  } catch (error) {
    ElMessage.error(
      `保存宿主配置失败：${error instanceof ApiError ? error.message : String(error)}`,
    )
  } finally {
    saving.value = false
  }
}

async function restart() {
  const ok = await confirmAction(
    '重启期间机器人会短暂离线，面板恢复后会自动刷新。',
    '重启 Tenko',
    '重启',
  )
  if (!ok || !(await requestRestart())) return
  await Promise.all([load(), refresh()])
}
</script>

<template>
  <PagePanel title="宿主配置">
    <template v-if="config.data.value" #extra>
      <el-button
        size="small"
        :disabled="!config.data.value.can_restart || restarting"
        :title="config.data.value.can_restart ? undefined : '当前不能由面板重启'"
        @click="restart"
      >
        重启 Tenko
      </el-button>
    </template>
    <ResourceBoundary :resource="config" label="宿主配置" :rows="4" :retry="load">
      <template #default="{ data }">
        <el-alert
          v-if="restarting || data.restart_required"
          type="warning"
          show-icon
          :closable="false"
          :title="restarting ? '正在重启…' : '配置已保存，重启后生效'"
          class="note"
          role="status"
        >
          <p v-if="restarting" class="text">面板恢复应答后会自动刷新，请稍候。</p>
          <template v-else>
            <p v-if="restartFailed" class="text">
              长时间没有等到面板恢复，请确认服务是否已重新启动，再刷新页面。
            </p>
            <el-button v-if="data.can_restart" size="small" @click="restart">重启</el-button>
            <p v-else class="text">当前不能由面板重启，请手动重启 Tenko。</p>
          </template>
        </el-alert>
        <el-alert
          v-if="!data.writable"
          type="info"
          show-icon
          :closable="false"
          title="配置文件不可写，宿主配置只能查看，请直接编辑配置文件。"
          class="note"
        />
        <p class="desc">写入配置文件，所有修改在重启后生效。</p>

        <el-form label-position="top" :disabled="!writable" @submit.prevent="save">
          <section v-for="group in groups" :key="group.title" class="group">
            <h4>{{ group.title }}</h4>
            <div class="fields">
              <el-form-item v-for="field in group.fields" :key="field.key" :label="field.label">
                <el-switch
                  v-if="field.kind === 'boolean'"
                  :model-value="current(field) as boolean"
                  :disabled="field.read_only"
                  :aria-label="field.label"
                  @update:model-value="edit(field, $event)"
                />
                <el-input-number
                  v-else-if="field.kind === 'integer' || field.kind === 'number'"
                  :model-value="current(field) as number"
                  :min="field.min ?? undefined"
                  :max="field.max ?? undefined"
                  :step="1"
                  :precision="field.kind === 'integer' ? 0 : undefined"
                  :disabled="field.read_only"
                  :aria-label="field.label"
                  @update:model-value="edit(field, $event)"
                />
                <el-select
                  v-else-if="field.kind === 'enum'"
                  :model-value="current(field) as string"
                  :disabled="field.read_only"
                  :aria-label="field.label"
                  @update:model-value="edit(field, $event)"
                >
                  <el-option
                    v-for="option in field.options"
                    :key="option.value"
                    :label="option.label"
                    :value="option.value"
                  />
                </el-select>
                <el-input
                  v-else-if="field.kind === 'urls'"
                  type="textarea"
                  :rows="3"
                  :model-value="current(field) as string"
                  placeholder="每行一个 https 地址"
                  :disabled="field.read_only"
                  :aria-label="field.label"
                  @update:model-value="edit(field, $event)"
                />
                <el-input
                  v-else
                  :model-value="current(field) as string"
                  placeholder="群号，留空表示不设置"
                  :disabled="field.read_only"
                  :aria-label="field.label"
                  @update:model-value="edit(field, $event)"
                />
                <p v-if="field.read_only" class="hint">引用了环境变量，请在配置文件里修改</p>
                <p v-if="field.help" class="hint">{{ field.help }}</p>
              </el-form-item>
            </div>
          </section>

          <div class="actions">
            <el-button
              type="primary"
              :disabled="!dirty || !writable"
              :loading="saving"
              @click="save"
            >
              保存宿主配置
            </el-button>
            <el-button :disabled="!dirty || saving" @click="draft = {}">放弃修改</el-button>
          </div>
        </el-form>
      </template>
    </ResourceBoundary>
  </PagePanel>
</template>

<style scoped>
.note {
  margin-bottom: 12px;
}
.desc,
.hint,
.text {
  margin: 0;
  color: var(--tk-muted);
  font-size: 12px;
}
.text {
  margin: 4px 0;
  color: inherit;
  line-height: 1.65;
}
.hint {
  width: 100%;
  margin-top: 4px;
  line-height: 1.5;
}
.group {
  margin-top: 18px;
}
.group h4 {
  margin: 0 0 10px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--tk-rule);
  color: var(--tk-heading);
  font-size: 13px;
  letter-spacing: 2px;
}
.fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 24px;
}
.fields :deep(.el-select),
.fields :deep(.el-input-number) {
  width: 100%;
  max-width: 260px;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 12px;
  margin-top: 8px;
}
@media (max-width: 1100px) {
  .fields {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
