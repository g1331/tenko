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
const { sync, lastSynced, refreshing, refresh } = usePanelData()

const meta = computed(() => route.meta)
const username = computed(() => auth.state.username || '—')

const syncText = computed(() => {
  const { state, ready, total } = sync.value
  if (state === 'syncing') return '正在同步…'
  if (state === 'offline') return '数据暂不可用'
  if (state === 'partial') return `部分数据不可用 · ${ready}/${total} 已同步`
  return lastSynced.value ? `已同步 · ${clockText(lastSynced.value)}` : '已连接'
})

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
        <span class="dot" :class="`is-${sync.state}`" aria-hidden="true" />
        <span class="sync-text">{{ syncText }}</span>
      </div>
      <el-tooltip content="立即刷新" placement="bottom">
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
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
  color: var(--tk-muted);
  font-size: 12px;
}
.sync-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dot {
  width: 8px;
  height: 8px;
  flex: 0 0 8px;
  border-radius: 50%;
  background: var(--tk-gold);
}
.dot.is-ok {
  background: var(--tk-ok-solid);
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

@media (max-width: 820px) {
  .tools {
    width: 100%;
    justify-content: space-between;
  }
  .sync-status {
    margin-right: auto;
  }
}
@media (max-width: 520px) {
  h1 {
    font-size: 22px;
  }
  .sync-text {
    max-width: 130px;
  }
  .user-name {
    max-width: 70px;
  }
}
</style>
