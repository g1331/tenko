<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, type ConfigField, type PluginConfigData } from '@/api'
import { confirmAction } from '@/composables/confirm'
import { usePluginOps } from '@/composables/usePluginOps'

// 插件配置表单：按 JSON Schema 的 properties 生成，只有简单类型可以在面板里改。
const props = defineProps<{ pluginId: string | null; name: string }>()
const visible = defineModel<boolean>({ required: true })
const ops = usePluginOps()

const config = ref<PluginConfigData | null>(null)
// 表单值的类型随字段变化，由模板按 schema 的 type 选择控件。
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const form = ref<Record<string, any>>({})
const loading = ref(false)
const saving = ref(false)
const error = ref('')

const fields = computed(() => Object.entries(config.value?.schema?.properties ?? {}))
const isEditable = (key: string) => !!config.value?.editable?.includes(key)
const readOnly = computed(() => config.value?.read_only_reason ?? null)
const show = (value: unknown) => (typeof value === 'string' ? value : JSON.stringify(value))
const label = (key: string, field: ConfigField) => field.title || key

async function load() {
  if (!props.pluginId) return
  loading.value = true
  error.value = ''
  config.value = null
  try {
    config.value = await api.manage.pluginConfig(props.pluginId)
    form.value = { ...config.value.values }
  } catch (reason) {
    error.value = (reason as Error).message
  } finally {
    loading.value = false
  }
}
watch(visible, (open) => open && load())

async function save() {
  if (!props.pluginId || !config.value) return
  const ok = await confirmAction(
    `将保存 ${props.name}（${props.pluginId}）的配置，写入配置文件，重启后生效。`,
    '保存插件配置',
    '保存',
  )
  if (!ok) return
  const values = Object.fromEntries(Object.entries(form.value).filter(([key]) => isEditable(key)))
  saving.value = true
  try {
    await api.manage.savePluginConfig(props.pluginId, values)
    ops.markChanged()
    ElMessage.success('已保存，重启后生效')
    visible.value = false
  } catch (reason) {
    ElMessage.error(`保存配置失败：${(reason as Error).message}`)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-drawer
    v-model="visible"
    :title="`${name} · 配置`"
    direction="rtl"
    append-to-body
    size="min(480px, 100vw)"
  >
    <el-skeleton v-if="loading" :rows="4" animated />
    <el-empty v-else-if="error" :image-size="64" :description="`配置暂不可用：${error}`">
      <el-button size="small" @click="load">重试</el-button>
    </el-empty>
    <el-empty
      v-else-if="config && !config.available"
      :image-size="64"
      description="插件未加载，或没有可配置项。启用并重启后再来看看。"
    />
    <template v-else-if="config">
      <el-alert v-if="readOnly" type="warning" show-icon :closable="false" :title="readOnly" />
      <el-empty v-if="!fields.length" :image-size="64" description="这个插件没有配置项" />
      <el-form v-else label-position="top" class="form" @submit.prevent="save">
        <el-form-item v-for="[key, field] in fields" :key="key" :label="label(key, field)">
          <template v-if="isEditable(key)">
            <el-switch
              v-if="field.type === 'boolean'"
              v-model="form[key]"
              :disabled="!!readOnly"
              :aria-label="label(key, field)"
            />
            <el-select
              v-else-if="field.enum"
              v-model="form[key]"
              :disabled="!!readOnly"
              :aria-label="label(key, field)"
            >
              <el-option
                v-for="item in field.enum"
                :key="String(item)"
                :label="String(item)"
                :value="item"
              />
            </el-select>
            <el-input-number
              v-else-if="field.type === 'integer' || field.type === 'number'"
              v-model="form[key]"
              :precision="field.type === 'integer' ? 0 : undefined"
              :disabled="!!readOnly"
              :aria-label="label(key, field)"
            />
            <el-input
              v-else
              v-model="form[key]"
              :disabled="!!readOnly"
              :aria-label="label(key, field)"
            />
          </template>
          <template v-else>
            <el-input :model-value="show(form[key])" readonly :aria-label="label(key, field)" />
            <p class="hint">这一项不能在面板里修改，请直接编辑配置文件。</p>
          </template>
          <p v-if="field.description" class="hint">{{ field.description }}</p>
        </el-form-item>
      </el-form>
    </template>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button
        type="primary"
        :loading="saving"
        :disabled="!config?.available || !!readOnly || !fields.length"
        @click="save"
      >
        保存配置
      </el-button>
    </template>
  </el-drawer>
</template>

<style scoped>
.form {
  margin-top: 14px;
}
.hint {
  width: 100%;
  margin: 4px 0 0;
  color: var(--tk-muted);
  font-size: 12px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
</style>
