<script setup lang="ts">
import { computed } from 'vue'
import type { PluginTask } from '@/api'
import { confirmAction } from '@/composables/confirm'
import { usePluginOps } from '@/composables/usePluginOps'

// 插件页顶部的两条提示：后台任务的进度与结果、需要重启的提醒。
const ops = usePluginOps()
const task = computed(() => ops.task.value)
const canRestart = computed(() => !!ops.installed.data.value?.can_restart)

const ACTIONS: Record<PluginTask['action'], string> = {
  install: '安装',
  update: '更新',
  resume: '恢复',
  uninstall: '卸载',
  rollback: '回滚',
  sync: '同步插件环境',
}
const taskName = (item: PluginTask) =>
  item.plugin_id
    ? `${ACTIONS[item.action]} ${item.plugin_id}`
    : item.action === 'update'
      ? '更新全部插件'
      : ACTIONS[item.action]
const version = (value: string | null, fallback: string) => value ?? fallback
/** 更新、恢复后每个插件是否已在运行中生效。 */
function liveText(item: PluginTask, plugin: string) {
  if (!item.live || !(plugin in item.live)) return ''
  const reason = item.live[plugin]
  return reason ? `（重启后生效：${reason}）` : '（已生效）'
}

async function restart() {
  const ok = await confirmAction(
    '重启期间机器人会短暂离线，面板恢复后会自动刷新。',
    '重启 Tenko',
    '重启',
  )
  if (ok) await ops.restart()
}
</script>

<template>
  <div class="banners">
    <el-alert
      v-if="task"
      :type="task.state === 'running' ? 'info' : task.state === 'succeeded' ? 'success' : 'error'"
      show-icon
      :closable="task.state !== 'running'"
      :title="
        task.state === 'running'
          ? `正在${taskName(task)}…`
          : task.state === 'succeeded'
            ? `已完成：${taskName(task)}`
            : `${taskName(task)}失败`
      "
      role="status"
      @close="ops.dismissTask"
    >
      <el-progress
        v-if="task.state === 'running'"
        :percentage="100"
        :indeterminate="true"
        :duration="2"
        :show-text="false"
        :stroke-width="4"
      />
      <template v-else>
        <p v-if="task.message" class="text">{{ task.message }}</p>
        <ul v-if="task.changes.length" class="changes">
          <li v-for="change in task.changes" :key="change.plugin_id">
            {{ change.plugin_id }}：{{ version(change.from, '未安装') }} →
            {{ version(change.to, '已移除') }}{{ liveText(task, change.plugin_id) }}
          </li>
        </ul>
        <p v-if="task.state === 'failed' && task.restored === true" class="text">
          环境已恢复到操作之前的状态。
        </p>
        <p v-else-if="task.state === 'failed' && task.restored === false" class="text">
          环境没能自动恢复，可能处于不一致的状态。请在聊天里发送
          /同步插件环境，或到“扩展插件”标签页同步。
        </p>
      </template>
    </el-alert>

    <el-alert
      v-if="ops.restartNeeded.value || ops.restarting.value"
      type="warning"
      show-icon
      :closable="false"
      :title="ops.restarting.value ? '正在重启…' : '有插件变更需要重启后生效'"
      role="status"
    >
      <template v-if="ops.restarting.value">
        <p class="text">面板恢复应答后会自动刷新，请稍候。</p>
      </template>
      <template v-else>
        <p v-if="ops.restartFailed.value" class="text">
          长时间没有等到面板恢复，请确认服务是否已重新启动，再刷新页面。
        </p>
        <el-button v-if="canRestart" size="small" :disabled="ops.busy.value" @click="restart">
          重启
        </el-button>
        <p v-else class="text">当前不能由面板重启，请手动重启 Tenko。</p>
      </template>
    </el-alert>
  </div>
</template>

<style scoped>
.banners {
  display: grid;
  gap: 12px;
}
.banners:not(:empty) {
  margin-bottom: 16px;
}
.text {
  margin: 4px 0;
  font-size: 12px;
  line-height: 1.65;
  overflow-wrap: anywhere;
}
.changes {
  margin: 4px 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.65;
}
</style>
