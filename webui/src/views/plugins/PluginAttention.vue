<script setup lang="ts">
import { computed, onMounted, reactive, watch } from 'vue'
import { api, type PluginFailure, type SuspendedPlugin } from '@/api'
import PagePanel from '@/components/PagePanel.vue'
import { confirmAction } from '@/composables/confirm'
import { relativeText, timeText } from '@/composables/format'
import { usePanelData } from '@/composables/usePanelData'
import { usePluginOps } from '@/composables/usePluginOps'
import PluginIcon from './PluginIcon.vue'

// 待处理里的插件：升级宿主时停用的（装上兼容版本即恢复）和本次启动加载失败的。
const ops = usePluginOps()
const { overview } = usePanelData()
onMounted(ops.loadInstalled)
// 总览里的待处理插件数变化（比如重启后自动恢复了）时重新读取。
watch(() => overview.data.value?.plugin_attention, ops.loadInstalled)

const suspended = computed(() => ops.installed.data.value?.suspended ?? [])
const failures = computed(() => ops.installed.data.value?.failures ?? [])
const total = computed(() => suspended.value.length + failures.value.length)

/** 恢复时选的版本；默认最旧的能用版本，功能变化最小。 */
const picked = reactive<Record<string, string>>({})
const versionsOf = (item: SuspendedPlugin) => [...item.options].reverse()
watch(
  suspended,
  (items) => {
    for (const item of items) {
      if (!item.options.includes(picked[item.plugin_id] ?? ''))
        picked[item.plugin_id] = versionsOf(item)[0] ?? ''
    }
  },
  { immediate: true },
)
const title = (item: { name: string | null; plugin_id: string }) => item.name || item.plugin_id
// 未收录的插件目录里没有版本可选，按原来的来源重新解析。
const byOriginal = (item: SuspendedPlugin) => item.trust === 'unlisted'
const canResume = (item: SuspendedPlugin) => byOriginal(item) || item.options.length > 0

async function resume(item: SuspendedPlugin) {
  const version = byOriginal(item) ? undefined : picked[item.plugin_id]
  const target = version ? ` ${version}` : '（按原来的来源）'
  const ok = await confirmAction(
    `为 ${title(item)} 装上${target}，重启后生效${item.enabled ? '，并按停用前的状态启用' : ''}。`,
    '恢复插件',
    '恢复',
  )
  if (ok) await ops.run({ action: 'resume', plugin_id: item.plugin_id, version })
}

async function uninstall(item: { plugin_id: string; name: string | null }) {
  const ok = await confirmAction(
    `卸载 ${title(item)}？插件数据保留，之后重新安装还能用。`,
    '卸载插件',
    '卸载',
  )
  if (ok) await ops.run({ action: 'uninstall', plugin_id: item.plugin_id })
}

async function disable(item: PluginFailure) {
  const ok = await confirmAction(
    `停用 ${title(item)}，下次启动不再尝试加载它。`,
    '停用插件',
    '停用',
  )
  if (!ok) return
  try {
    await api.manage.togglePlugin(item.plugin_id, { enabled: false, confirm: true })
    ElMessage.success(`已停用 ${title(item)}`)
  } catch (error) {
    ElMessage.error(`停用失败：${(error as Error).message}`)
  }
  await ops.loadInstalled()
}
</script>

<template>
  <PagePanel v-if="total" title="插件">
    <template #extra>共 {{ total }} 项</template>
    <ul class="list">
      <li v-for="item in suspended" :key="`s:${item.plugin_id}`" class="item">
        <PluginIcon :name="title(item)" :src="item.icon_url" :size="36" />
        <div class="body">
          <div class="head">
            <strong class="name">{{ title(item) }}</strong>
            <span class="tk-mono sub">{{ item.plugin_id }} {{ item.version }}</span>
            <el-tag size="small" type="warning" effect="plain" disable-transitions>
              已停用 · 不兼容
            </el-tag>
          </div>
          <p class="text">{{ item.error || item.reason || '与当前版本不兼容' }}</p>
          <p v-if="item.resume" class="meta">重启后会自动装上 {{ item.resume }}</p>
          <p v-else class="meta">
            记录、数据和配置都保留
            <template v-if="item.suspended_at">
              ·
              <span :title="timeText(item.suspended_at)">{{
                relativeText(item.suspended_at)
              }}</span>
              停用
            </template>
          </p>
        </div>
        <div class="actions">
          <el-select
            v-if="item.options.length"
            v-model="picked[item.plugin_id]"
            size="small"
            class="version"
            :aria-label="`${title(item)} 要装的版本`"
            :disabled="ops.busy.value"
          >
            <el-option
              v-for="(version, index) in versionsOf(item)"
              :key="version"
              :value="version"
              :label="`${version}${index === 0 ? '（改动最小）' : ''}`"
            />
          </el-select>
          <span v-else-if="!byOriginal(item)" class="meta">暂无兼容版本</span>
          <el-button
            v-if="canResume(item)"
            size="small"
            type="primary"
            :disabled="ops.busy.value"
            @click="resume(item)"
          >
            恢复
          </el-button>
          <el-button size="small" :disabled="ops.busy.value" @click="uninstall(item)">
            卸载
          </el-button>
        </div>
      </li>
      <li v-for="item in failures" :key="`f:${item.plugin_id}`" class="item">
        <PluginIcon :name="title(item)" :src="null" :size="36" />
        <div class="body">
          <div class="head">
            <strong class="name">{{ title(item) }}</strong>
            <span class="tk-mono sub">{{ item.plugin_id }}</span>
            <el-tag size="small" type="danger" effect="plain" disable-transitions>
              加载失败
            </el-tag>
          </div>
          <p class="text tk-mono">{{ item.reason }}</p>
          <p class="meta">
            只跳过了这个插件，其他功能正常。{{
              item.builtin ? '内置插件需要修复后重启。' : '可以停用它，或卸载后重新安装。'
            }}
          </p>
        </div>
        <div v-if="!item.builtin" class="actions">
          <el-button size="small" :disabled="ops.busy.value" @click="disable(item)">
            停用
          </el-button>
          <el-button size="small" :disabled="ops.busy.value" @click="uninstall(item)">
            卸载
          </el-button>
        </div>
      </li>
    </ul>
  </PagePanel>
</template>

<style scoped>
.list {
  display: grid;
  margin: 0;
  padding: 0;
  list-style: none;
}
.item {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 0;
  border-top: 1px solid var(--tk-line);
}
.item:first-child {
  border-top: none;
  padding-top: 4px;
}
.body {
  flex: 1;
  min-width: 200px;
}
.head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
}
.name {
  color: var(--tk-heading);
}
.sub,
.meta {
  color: var(--tk-muted);
  font-size: 12px;
}
.text {
  margin: 4px 0 0;
  font-size: 13px;
  overflow-wrap: anywhere;
}
.meta {
  margin: 4px 0 0;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.actions .el-button + .el-button {
  margin-left: 0;
}
.version {
  width: 150px;
}
/* 窄屏时操作换到下一行，与正文对齐（图标 36px + 间距 12px）。 */
@media (max-width: 640px) {
  .actions {
    width: 100%;
    padding-left: 48px;
  }
}
</style>
