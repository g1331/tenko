<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { useAuth } from '@/composables/useAuth'
import { usePanelData } from '@/composables/usePanelData'
import { clockText, timeText } from '@/composables/format'

const route = useRoute()
const router = useRouter()
const auth = useAuth()
const { sync, live, lastSynced, refreshing, refresh, retryLive } = usePanelData()

const meta = computed(() => route.meta)
const username = computed(() => auth.state.username || '—')

// 只在需要提醒时显示状态：实时连接中断优先，其次是部分数据读取失败。
const warning = computed(() => {
  if (live.value === 'down') return '实时连接中断，正在重连…'
  const { state, ready, total } = sync.value
  if (state === 'offline') return '数据暂不可用'
  if (state === 'partial') return `部分数据不可用 · ${ready}/${total} 已同步`
  return ''
})
const refreshTip = computed(() =>
  lastSynced.value ? `上次同步 ${clockText(lastSynced.value)}` : '立即刷新',
)

function goSecurity() {
  void router.push({ name: 'settings', query: { section: 'account-security' } })
}

async function logout() {
  try {
    await auth.logout()
  } catch (error) {
    // 401 已由全局处理回到登录页，此时不再提示。
    if (auth.isAuthenticated.value) ElMessage.error(`退出登录失败：${(error as Error).message}`)
    return
  }
  ElMessage.success('已退出登录')
  await router.replace({ name: 'login' })
}
</script>

<template>
  <header class="page-head">
    <div class="title-block">
      <h1>{{ meta.title }}</h1>
      <p v-if="meta.subtitle">{{ meta.subtitle }}</p>
    </div>
    <div class="tools">
      <div class="sync-status" role="status" aria-live="polite">
        <button
          v-if="live === 'down'"
          class="warn-pill"
          type="button"
          :title="warning"
          @click="retryLive"
        >
          <span class="dot is-offline" aria-hidden="true" />{{ warning }}
        </button>
        <span v-else-if="warning" class="warn-pill is-static" :title="warning">
          <span class="dot" aria-hidden="true" />{{ warning }}
        </span>
      </div>
      <el-tooltip :content="refreshTip" placement="bottom">
        <el-button
          class="refresh-button"
          :icon="Refresh"
          :loading="refreshing"
          aria-label="立即刷新"
          @click="refresh"
        />
      </el-tooltip>
      <el-dropdown trigger="click" placement="bottom-end">
        <button class="user-button" type="button" aria-label="账户菜单">
          <span class="avatar" aria-hidden="true">{{ username.slice(0, 1) }}</span>
          <span class="user-name">{{ username }}</span>
        </button>
        <template #dropdown>
          <el-dropdown-menu class="user-menu">
            <div class="user-head">
              <small>已登录为</small>
              <strong>{{ username }}</strong>
              <small v-if="auth.state.expiresAt"
                >会话到期 {{ timeText(auth.state.expiresAt) }}</small
              >
            </div>
            <el-dropdown-item @click="goSecurity">账号与安全</el-dropdown-item>
            <el-dropdown-item class="is-danger" @click="logout">退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </header>
</template>

<style scoped>
.page-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px 20px;
  margin-bottom: 22px;
}
.title-block {
  min-width: 0;
}
h1 {
  margin: 0;
  color: var(--tk-heading);
  font-size: 26px;
  letter-spacing: 0.04em;
  line-height: 1.2;
}
.title-block p {
  margin: 4px 0 0;
  color: var(--tk-muted);
  font-size: 13px;
}
.tools {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
}
.sync-status {
  flex: 0 1 auto;
}
.warn-pill {
  display: inline-flex;
  max-width: 100%;
  align-items: center;
  gap: 8px;
  padding: 4px 12px;
  border: 0;
  border-radius: 14px;
  background: var(--tk-warn-bg);
  color: var(--tk-warn);
  white-space: nowrap;
  font: inherit;
  font-size: 12px;
  font-weight: 600;
}
button.warn-pill {
  cursor: pointer;
}
.dot {
  width: 8px;
  height: 8px;
  flex: 0 0 8px;
  border-radius: 50%;
  background: var(--tk-gold);
}
.dot.is-offline {
  background: var(--tk-coral);
}
.refresh-button {
  width: 34px;
  height: 34px;
  padding: 0;
  margin: 0;
  border-radius: 9px;
  border-color: var(--tk-line);
  background: var(--tk-surface);
  color: var(--tk-muted);
}
.user-button {
  display: inline-flex;
  height: 34px;
  align-items: center;
  gap: 8px;
  padding: 0 12px 0 4px;
  border: 1px solid var(--tk-line);
  border-radius: 17px;
  background: var(--tk-surface);
  color: var(--tk-heading);
  cursor: pointer;
  font: inherit;
  font-size: 13px;
}
.user-button:hover {
  border-color: var(--tk-line-strong);
  background: var(--tk-surface-2);
}
.avatar {
  display: grid;
  width: 26px;
  height: 26px;
  place-items: center;
  border-radius: 50%;
  background: var(--tk-primary);
  color: var(--tk-on-primary);
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
}
.user-name {
  max-width: 140px;
  overflow: hidden;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.user-head {
  display: grid;
  min-width: 200px;
  padding: 8px 16px 10px;
  border-bottom: 1px solid var(--tk-line);
}
.user-head small {
  color: var(--tk-muted);
  font-size: 12px;
}
.user-head strong {
  overflow: hidden;
  color: var(--tk-heading);
  font-size: 15px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
:global(.user-menu .el-dropdown-menu__item.is-danger) {
  color: var(--tk-danger);
}

/* 窄屏：按钮留在标题右上角，不单独占一行 */
@media (max-width: 820px) {
  .page-head {
    flex-wrap: nowrap;
    align-items: flex-start;
  }
  .title-block {
    flex: 1;
  }
  .tools {
    flex: none;
    gap: 8px;
  }
  .warn-pill {
    max-width: 150px;
    overflow: hidden;
    text-overflow: ellipsis;
  }
}
@media (max-width: 520px) {
  h1 {
    font-size: 22px;
  }
  .page-head {
    margin-bottom: 16px;
  }
  .user-button {
    padding: 0 3px;
  }
  .user-name {
    display: none;
  }
  /* 只留状态圆点，完整文字在 title 里 */
  .warn-pill {
    gap: 0;
    padding: 9px;
    font-size: 0;
  }
}
</style>
