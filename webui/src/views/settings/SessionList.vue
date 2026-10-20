<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, ApiError, type SessionInfo } from '@/api'
import PagePanel from '@/components/PagePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import { confirmAction } from '@/composables/confirm'
import { deviceText, relativeText, timeText } from '@/composables/format'

const sessions = ref<SessionInfo[] | null>(null)
const error = ref('')
const loading = ref(false)
const working = ref(false)

const others = computed(() => (sessions.value ?? []).filter((item) => !item.current).length)

async function reload() {
  loading.value = true
  try {
    sessions.value = (await api.auth.sessions()).sessions
    error.value = ''
  } catch (reason) {
    error.value = reason instanceof ApiError ? reason.message : String(reason)
  } finally {
    loading.value = false
  }
}
defineExpose({ reload })
onMounted(reload)

/** 确认 → 执行 → 提示 → 重新读取列表；取消确认框不算错误。 */
async function confirmAndRun(message: string, title: string, run: () => Promise<string>) {
  if (!(await confirmAction(message, title, '注销'))) return
  working.value = true
  try {
    ElMessage.success(await run())
  } catch (reason) {
    ElMessage.error(`注销失败：${reason instanceof ApiError ? reason.message : String(reason)}`)
  } finally {
    working.value = false
    await reload()
  }
}

function revoke(item: SessionInfo) {
  const label = `${deviceText(item.user_agent)}（${item.ip || '未知 IP'}）`
  return confirmAndRun(
    `确定注销 ${label} 的登录会话吗？该设备需要重新登录。`,
    '注销会话',
    async () => {
      await api.auth.revokeSession(item.id)
      return '会话已注销'
    },
  )
}

function revokeOthers() {
  return confirmAndRun(
    `确定注销其他 ${others.value} 个会话吗？这些设备都需要重新登录，当前会话保持不变。`,
    '注销其他会话',
    async () => `已注销 ${(await api.auth.revokeOthers()).revoked} 个其他会话`,
  )
}
</script>

<template>
  <PagePanel title="登录会话">
    <template #extra>
      <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
    </template>
    <p class="hint">已登录的浏览器与设备。看到不认识的会话时请立即注销，并修改密码。</p>

    <el-skeleton v-if="!sessions && !error" :rows="3" animated />
    <el-empty v-else-if="!sessions" :image-size="64" :description="`读取会话失败：${error}`">
      <el-button size="small" @click="reload">重试</el-button>
    </el-empty>
    <el-empty v-else-if="!sessions.length" :image-size="64" description="暂无有效会话" />
    <template v-else>
      <ul class="list">
        <li v-for="item in sessions" :key="item.id" class="item" :class="{ current: item.current }">
          <div class="head">
            <div class="who">
              <div class="title">
                {{ deviceText(item.user_agent) }}
                <StatusTag v-if="item.current" kind="on">当前</StatusTag>
              </div>
              <div class="ua" :title="item.user_agent">
                {{ item.user_agent || '无 User-Agent' }}
              </div>
            </div>
            <el-tooltip
              :disabled="!item.current"
              content="当前会话请使用“退出登录”"
              placement="top"
            >
              <span>
                <el-button
                  size="small"
                  type="danger"
                  plain
                  :disabled="item.current || working"
                  @click="revoke(item)"
                >
                  注销
                </el-button>
              </span>
            </el-tooltip>
          </div>
          <el-descriptions :column="2" direction="vertical" size="small">
            <el-descriptions-item label="IP">{{ item.ip || '—' }}</el-descriptions-item>
            <el-descriptions-item label="最近活动">
              <span :title="timeText(item.last_seen_at)">{{
                relativeText(item.last_seen_at)
              }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="登录于">{{
              timeText(item.created_at)
            }}</el-descriptions-item>
            <el-descriptions-item label="到期">{{
              timeText(item.expires_at)
            }}</el-descriptions-item>
          </el-descriptions>
        </li>
      </ul>
      <el-button :disabled="!others || working" @click="revokeOthers">
        注销其他会话{{ others ? `（${others}）` : '' }}
      </el-button>
    </template>
  </PagePanel>
</template>

<style scoped>
.hint {
  margin: 0 0 12px;
  color: var(--tk-muted);
  font-size: 12px;
}
.list {
  margin: 0 0 14px;
  padding: 0;
  list-style: none;
}
.item {
  padding: 12px 14px;
  border: 1px solid var(--tk-line);
  border-radius: 4px;
}
.item + .item {
  margin-top: 10px;
}
.item.current {
  background: var(--tk-ok-bg);
}
.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}
.who {
  min-width: 0;
}
.title {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--tk-heading);
  font-weight: 700;
}
.ua {
  overflow: hidden;
  color: var(--tk-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.item :deep(.el-descriptions__body) {
  background: transparent;
}
.item :deep(.el-descriptions__label) {
  color: var(--tk-muted);
  font-size: 12px;
  font-weight: 400;
}
.item
  :deep(.el-descriptions__label.el-descriptions__cell:not(.is-bordered-label).is-vertical-label) {
  padding-bottom: 1px;
}
.item :deep(.el-descriptions__body .el-descriptions__table .el-descriptions__cell) {
  line-height: 1.4;
}
.item :deep(.el-descriptions__content.is-vertical-content) {
  padding-bottom: 8px;
}
.item :deep(.el-descriptions__table tr:last-child .is-vertical-content) {
  padding-bottom: 0;
}
</style>
