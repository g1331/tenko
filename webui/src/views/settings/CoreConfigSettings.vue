<script lang="ts">
/** 给设置页目录和搜索用的分组索引：text 是分组内全部字段名和说明。 */
export interface CoreConfigIndex {
  key: string
  title: string
  text: string
}
</script>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
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
  ['chat_log', '聊天记录'],
  ['exception', '异常报告'],
  ['registry', '插件市场'],
]

const props = defineProps<{ query?: string }>()
const emit = defineEmits<{ index: [groups: CoreConfigIndex[]] }>()

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
const fieldText = (field: CoreConfigField) =>
  [field.label, field.help, field.key, ...field.options.map((o) => o.label)].join(' ')
const allGroups = computed(() =>
  GROUPS.map(([key, title]) => ({
    key,
    title,
    fields: (config.data.value?.fields ?? []).filter((field) => field.group === key),
  })).filter((item) => item.fields.length),
)
watch(
  allGroups,
  (items) =>
    emit(
      'index',
      items.map(({ key, title, fields }) => ({
        key,
        title,
        text: fields.map(fieldText).join(' '),
      })),
    ),
  { immediate: true },
)
const terms = computed(() => (props.query ?? '').trim().toLowerCase().split(/\s+/).filter(Boolean))
const hit = (text: string) => terms.value.every((term) => text.toLowerCase().includes(term))
/** 搜索时：组名命中显示整组，否则只显示命中的字段。 */
const groups = computed(() =>
  allGroups.value
    .map((group) => ({
      ...group,
      fields: hit(`宿主配置 ${group.title}`)
        ? group.fields
        : group.fields.filter((field) => hit(`宿主配置 ${group.title} ${fieldText(field)}`)),
    }))
    .filter((group) => group.fields.length),
)
const dirtyCount = computed(() => Object.keys(draft.value).length)

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
    const saved = await api.manage.saveConfig(values)
    config.data.value = saved
    draft.value = {}
    if (saved.restart_required) ElMessage.warning(restartTitle(saved))
    else ElMessage.success('宿主配置已保存并生效')
  } catch (error) {
    ElMessage.error(
      `保存宿主配置失败：${error instanceof ApiError ? error.message : String(error)}`,
    )
  } finally {
    saving.value = false
  }
}

// 大部分项保存即生效；只有返回的这些项要重启，"*" 表示整份配置都要重启。
function restartTitle(data: CoreConfigData) {
  const fields = data.restart_fields ?? []
  if (!fields.length || fields.includes('*')) return '配置已保存，重启后生效'
  const names = fields.map((key) => data.fields.find((f) => f.key === key)?.label ?? key)
  return `已保存；${names.map((name) => `“${name}”`).join('、')}重启后生效，其余已生效`
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
          :title="restarting ? '正在重启…' : restartTitle(data)"
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
        <p class="desc">写入配置文件，保存后立即生效；“启用检查更新”需要重启。</p>

        <el-form label-position="top" :disabled="!writable" @submit.prevent="save">
          <section
            v-for="group in groups"
            :id="`set-core-${group.key}`"
            :key="group.key"
            class="group core-group"
          >
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

          <div class="actions" :class="{ floating: dirty }">
            <span v-if="dirty" class="pending">已修改 {{ dirtyCount }} 项，保存后生效</span>
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
  border-bottom: 1px solid var(--tk-line);
  color: var(--tk-heading);
  font-size: 13px;
  letter-spacing: 0.04em;
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
  align-items: center;
  gap: 10px 12px;
  margin-top: 8px;
}
/* 有未保存的修改时吸附在视口底部，不用滚到最后才能保存。 */
.actions.floating {
  position: sticky;
  bottom: 12px;
  z-index: 3;
  padding: 10px 14px;
  border: 1px solid var(--tk-line);
  border-radius: 10px;
  background: var(--tk-surface);
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.14);
}
.pending {
  margin-right: auto;
  color: var(--tk-heading);
  font-size: 13px;
}
@media (max-width: 1100px) {
  .fields {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
