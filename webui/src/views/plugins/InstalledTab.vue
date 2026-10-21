<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, type InstalledPlugin } from '@/api'
import ResourceBoundary from '@/components/ResourceBoundary.vue'
import StatusTag from '@/components/StatusTag.vue'
import { confirmAction } from '@/composables/confirm'
import { usePanelData } from '@/composables/usePanelData'
import { usePluginOps } from '@/composables/usePluginOps'
import { type StatusInfo } from '@/composables/status'
import PluginConfigDrawer from './PluginConfigDrawer.vue'
import PluginIcon from './PluginIcon.vue'
import TierTag from './TierTag.vue'

// 通过插件管理器安装的第三方插件：启停、更新、配置、卸载，以及整体的更新、回滚和环境同步。
const ops = usePluginOps()
const { refresh } = usePanelData()

const toggling = ref('')
const configOf = ref<InstalledPlugin | null>(null)
const configOpen = ref(false)
const removing = ref<InstalledPlugin | null>(null)
const removeOpen = ref(false)
const clearData = ref(false)

const displayName = (item: InstalledPlugin) => item.name || item.plugin_id
const updatable = computed(
  () => ops.installed.data.value?.plugins.filter((item) => item.update) ?? [],
)
const state = (item: InstalledPlugin): StatusInfo =>
  item.restart_required
    ? { kind: 'pending', label: '待重启' }
    : item.enabled
      ? { kind: 'on', label: '已启用' }
      : { kind: 'off', label: '未启用' }

async function toggle(item: InstalledPlugin) {
  const enabled = !item.enabled
  const title = enabled ? '启用插件' : '停用插件'
  const ok = await confirmAction(
    `${enabled ? '插件将在重启后开始响应。' : '插件将停止在所有群响应。'}插件：${displayName(item)}（${item.plugin_id}）。`,
    title,
  )
  if (!ok) return
  toggling.value = item.plugin_id
  try {
    const result = await api.manage.togglePlugin(item.plugin_id, { enabled, confirm: true })
    if (result.restart_required) {
      ops.markChanged()
      ElMessage.info(enabled ? '已启用，重启后生效' : '已停用，重启后生效')
    } else ElMessage.success(enabled ? '插件已启用' : '插件已停用')
    await Promise.all([ops.loadInstalled(), refresh()])
  } catch (error) {
    ElMessage.error(`${title}失败：${(error as Error).message}`)
  } finally {
    toggling.value = ''
  }
}

async function update(item: InstalledPlugin) {
  const ok = await confirmAction(
    `将把 ${displayName(item)} 从 ${item.version} 更新到 ${item.update}，在后台进行，完成后需要重启才会生效。`,
    '更新插件',
    '更新',
  )
  if (ok) await ops.run({ action: 'update', plugin_id: item.plugin_id })
}
async function updateAll() {
  const ok = await confirmAction(
    `将更新 ${updatable.value.length} 个插件到目录里的最新版本，在后台进行，完成后需要重启才会生效。`,
    '更新全部插件',
    '更新',
  )
  if (ok) await ops.run({ action: 'update' })
}
async function rollback() {
  const ok = await confirmAction(
    '将撤销最近一次插件安装、更新或卸载，版本、依赖和插件记录一起恢复，完成后需要重启才会生效。',
    '回滚上一次操作',
    '回滚',
  )
  if (ok) await ops.run({ action: 'rollback' })
}
async function sync() {
  const ok = await confirmAction(
    '将按插件锁重装与锁不一致的包，让运行环境恢复到记录的状态。',
    '同步插件环境',
    '同步',
  )
  if (ok) await ops.run({ action: 'sync' })
}

function askRemove(item: InstalledPlugin) {
  removing.value = item
  clearData.value = false
  removeOpen.value = true
}
async function remove() {
  const item = removing.value
  if (!item) return
  removeOpen.value = false
  await ops.run({ action: 'uninstall', plugin_id: item.plugin_id, clear_data: clearData.value })
}
</script>

<template>
  <ResourceBoundary :resource="ops.installed" label="已安装插件" :retry="ops.loadInstalled">
    <template #default="{ data }">
      <div class="toolbar">
        <span class="summary">共 {{ data.plugins.length }} 个第三方插件</span>
        <div class="bulk">
          <el-button
            v-if="data.rollback_available"
            size="small"
            :disabled="ops.busy.value"
            @click="rollback"
          >
            回滚上一次操作
          </el-button>
          <el-button
            size="small"
            type="primary"
            :disabled="ops.busy.value || !updatable.length"
            @click="updateAll"
          >
            更新全部
          </el-button>
        </div>
      </div>

      <el-alert
        v-if="data.drift.length"
        class="drift"
        type="warning"
        show-icon
        :closable="false"
        title="插件环境与插件锁不一致"
      >
        <p class="drift-text">以下包与记录的版本不同：{{ data.drift.join('、') }}</p>
        <el-button size="small" :disabled="ops.busy.value" @click="sync">同步插件环境</el-button>
      </el-alert>

      <el-empty
        v-if="!data.plugins.length"
        :image-size="64"
        description="还没有通过插件管理器安装的第三方插件，可以到“插件市场”看看。"
      />
      <div v-else class="grid">
        <article
          v-for="item in data.plugins"
          :key="item.plugin_id"
          class="installed-card"
          :data-plugin="item.plugin_id"
        >
          <header>
            <PluginIcon :name="displayName(item)" :src="item.icon_url" />
            <div class="title">
              <strong class="name">{{ displayName(item) }}</strong>
              <code class="key">{{ item.plugin_id }} · {{ item.version }}</code>
            </div>
            <StatusTag :kind="state(item).kind">{{ state(item).label }}</StatusTag>
          </header>
          <div class="flags">
            <TierTag :tier="item.trust" />
            <el-tag
              v-if="item.update"
              size="small"
              type="success"
              effect="plain"
              disable-transitions
            >
              可更新到 {{ item.update }}
            </el-tag>
          </div>
          <p v-if="item.blocked" class="note danger">已被目录封禁：{{ item.blocked }}</p>
          <p v-if="item.yanked" class="note warn">此版本已撤回：{{ item.yanked }}</p>
          <div class="actions">
            <el-button
              size="small"
              :loading="toggling === item.plugin_id"
              :disabled="ops.busy.value || !!toggling"
              @click="toggle(item)"
            >
              {{ item.enabled ? '停用' : '启用' }}
            </el-button>
            <el-button
              v-if="item.update"
              size="small"
              type="primary"
              :disabled="ops.busy.value"
              @click="update(item)"
            >
              更新
            </el-button>
            <el-button
              size="small"
              :disabled="ops.busy.value"
              @click="((configOf = item), (configOpen = true))"
            >
              配置
            </el-button>
            <el-button
              size="small"
              type="danger"
              plain
              :disabled="ops.busy.value"
              @click="askRemove(item)"
            >
              卸载
            </el-button>
          </div>
        </article>
      </div>
    </template>
  </ResourceBoundary>

  <PluginConfigDrawer
    v-model="configOpen"
    :plugin-id="configOf?.plugin_id ?? null"
    :name="configOf ? displayName(configOf) : ''"
  />

  <el-dialog
    v-model="removeOpen"
    title="卸载插件"
    width="min(460px, calc(100vw - 24px))"
    align-center
  >
    <template v-if="removing">
      <p class="dialog-text">
        将卸载 {{ displayName(removing) }}（{{
          removing.plugin_id
        }}）。卸载在后台进行，运行中的插件要重启后才会真正移除；数据默认保留。
      </p>
      <el-checkbox v-model="clearData" :disabled="removing.loaded">同时清除数据</el-checkbox>
      <p v-if="removing.loaded" class="dialog-hint">
        插件正在运行，仍占用着数据，暂时不能清除。可以先卸载，重启后再用 /清除插件数据 清理。
      </p>
    </template>
    <template #footer>
      <el-button @click="removeOpen = false">取消</el-button>
      <el-button type="danger" @click="remove">卸载</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 12px;
  margin-bottom: 16px;
}
.summary {
  color: var(--tk-muted);
  font-size: 12px;
}
.bulk {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.bulk .el-button {
  margin: 0;
}
.drift {
  margin-bottom: 16px;
}
.drift-text {
  margin: 0 0 8px;
  overflow-wrap: anywhere;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 320px), 1fr));
  gap: 16px;
}
.installed-card {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 10px;
  padding: 16px 18px;
  border: 1px solid var(--tk-line);
  border-radius: var(--tk-radius);
  background: var(--tk-surface);
  box-shadow: var(--tk-shadow);
}
header {
  display: flex;
  align-items: center;
  gap: 10px;
}
.title {
  min-width: 0;
  flex: 1;
}
.name {
  display: block;
  color: var(--tk-heading);
  font-size: 15px;
  overflow-wrap: anywhere;
}
.key {
  display: block;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.flags {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
.note {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.note.danger {
  color: var(--tk-danger);
}
.note.warn {
  color: var(--tk-warn);
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: auto;
  padding-top: 12px;
  border-top: 1px solid var(--tk-line);
}
.actions .el-button {
  margin: 0;
}
.dialog-text {
  margin: 0 0 14px;
  line-height: 1.7;
}
.dialog-hint {
  margin: 8px 0 0;
  color: var(--tk-muted);
  font-size: 12px;
  line-height: 1.65;
}
@media (max-width: 520px) {
  .grid {
    gap: 12px;
  }
}
</style>
