<script setup lang="ts">
import { computed, type Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Box,
  ChatDotRound,
  CircleClose,
  Connection,
  Message,
  Odometer,
  Setting,
  Switch,
  Tickets,
  Upload,
  User,
} from '@element-plus/icons-vue'
import GithubLink from '@/components/GithubLink.vue'
import ThemeSwitch from '@/components/ThemeSwitch.vue'
import tenshi from '@/assets/tenshi.webp'
import AppHeader from './AppHeader.vue'
import { usePanelData, usePanelPolling } from '@/composables/usePanelData'

// 已登录区域的外壳：加载并定时刷新共享数据，卸载（登出 / 会话失效）时清空。
usePanelPolling()
const { overview } = usePanelData()

const route = useRoute()
const router = useRouter()
// 子页面（如 /plugins/xxx）高亮所属的导航项。
const activeNav = computed(() => `/${route.path.split('/')[1] ?? ''}`)

const icons: Record<string, Component> = {
  overview: Odometer,
  inbox: Message,
  chat: ChatDotRound,
  accounts: User,
  plugins: Connection,
  switches: Switch,
  blacklist: CircleClose,
  upgrade: Upload,
  logs: Tickets,
  sandbox: Box,
  settings: Setting,
}

interface NavItem {
  path: string
  label: string
  short: string
  icon: Component
  group: string
  badge: number
}
// 路由表顺序即导航顺序。
const navItems = computed<NavItem[]>(() =>
  router
    .getRoutes()
    .filter((item) => item.meta.nav)
    .map((item) => ({
      path: item.path,
      label: item.meta.nav!.label,
      short: item.meta.nav!.short,
      icon: icons[item.name as string] ?? Odometer,
      group: item.meta.nav!.group,
      badge: item.name === 'inbox' ? (overview.data.value?.inbox ?? 0) : 0,
    })),
)
const groups = computed(() =>
  [...new Set(navItems.value.map((item) => item.group))].map((title) => ({
    title,
    items: navItems.value.filter((item) => item.group === title),
  })),
)

const version = computed(() => {
  const value = overview.data.value?.version
  return value ? `v${value}` : ''
})
</script>

<template>
  <div class="app-frame">
    <aside class="sidebar" aria-label="Tenko 功能导航">
      <div class="sidebar-art" :style="{ backgroundImage: `url(${tenshi})` }">
        <div class="sidebar-brand">
          <b>TENKO</b>
          <span>比那名居天子 · 群管家</span>
        </div>
      </div>

      <el-menu class="sidebar-menu" :default-active="activeNav" router aria-label="功能分类">
        <el-menu-item-group v-for="group in groups" :key="group.title">
          <template #title>{{ group.title }}</template>
          <el-menu-item v-for="item in group.items" :key="item.path" :index="item.path">
            <el-icon><component :is="item.icon" /></el-icon>
            <span>{{ item.label }}</span>
            <span v-if="item.badge" class="nav-badge">{{ item.badge }}</span>
          </el-menu-item>
        </el-menu-item-group>
      </el-menu>

      <div class="sidebar-foot">
        <ThemeSwitch />
        <GithubLink />
        <span class="version">{{ version }}</span>
      </div>
    </aside>

    <section class="workspace">
      <header class="mobile-topbar">
        <div class="mobile-brand"><b>TENKO</b><span>群管家</span></div>
        <div class="mobile-tools"><ThemeSwitch /><GithubLink /></div>
      </header>
      <el-menu
        class="mobile-nav"
        mode="horizontal"
        :ellipsis="false"
        :default-active="activeNav"
        router
        aria-label="移动端功能分类"
      >
        <el-menu-item v-for="item in navItems" :key="item.path" :index="item.path">
          {{ item.short }}
          <span v-if="item.badge" class="nav-badge">{{ item.badge }}</span>
        </el-menu-item>
      </el-menu>

      <main>
        <AppHeader />
        <router-view v-slot="{ Component }">
          <component :is="Component" :key="route.name" />
        </router-view>
      </main>
    </section>
  </div>
</template>

<style scoped>
.nav-badge {
  min-width: 18px;
  height: 18px;
  margin-left: auto;
  padding: 0 5px;
  border-radius: 9px;
  background: var(--el-color-danger);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  line-height: 18px;
  text-align: center;
}
.mobile-nav .nav-badge {
  margin-left: 4px;
}
.app-frame {
  display: grid;
  width: 100%;
  min-height: 100vh;
  grid-template-columns: 236px minmax(0, 1fr);
  background: var(--tk-bg);
}

/* ---------------- 侧栏 ---------------- */
.sidebar {
  position: sticky;
  top: 0;
  display: flex;
  height: 100dvh;
  align-self: start;
  flex-direction: column;
  overflow: hidden;
  background: var(--tk-sidebar-bg);
  color: var(--tk-side-text);
}
.sidebar > * {
  flex-shrink: 0;
}
.sidebar-art {
  position: relative;
  height: 176px;
  background-position: center 28%;
  background-size: cover;
}
.sidebar-art::after {
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, transparent 30%, var(--tk-sidebar-bg) 100%);
  content: '';
}
.sidebar-brand {
  position: absolute;
  bottom: 12px;
  left: 22px;
  z-index: 1;
  display: grid;
}
.sidebar-brand b {
  color: #fff;
  font-family: var(--tk-font-num);
  font-size: 24px;
  font-weight: 700;
  letter-spacing: 0.32em;
  line-height: 1.1;
  text-shadow: 0 1px 6px rgba(8, 12, 30, 0.45);
}
.sidebar-brand span {
  color: var(--tk-side-text);
  font-size: 11px;
  letter-spacing: 0.14em;
  opacity: 0.85;
}
.sidebar-menu {
  --el-menu-bg-color: transparent;
  --el-menu-text-color: var(--tk-side-text);
  --el-menu-hover-bg-color: rgba(255, 255, 255, 0.05);
  --el-menu-hover-text-color: #fff;
  --el-menu-active-color: #fff;
  --el-menu-item-height: 42px;
  flex: 1 1 auto;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  padding: 10px 12px;
  border: 0;
}
.sidebar-menu :deep(.el-menu-item-group__title) {
  padding: 0 12px 6px;
  color: var(--tk-side-muted);
  font-size: 11px;
  letter-spacing: 0.16em;
}
.sidebar-menu :deep(.el-menu-item-group:not(:first-child) .el-menu-item-group__title) {
  padding-top: 14px;
}
.sidebar-menu :deep(.el-menu-item) {
  position: relative;
  margin-bottom: 2px;
  padding: 0 12px;
  border-radius: 9px;
  font-size: 14px;
  gap: 12px;
}
.sidebar-menu :deep(.el-menu-item .el-icon) {
  width: 18px;
  margin: 0;
  font-size: 18px;
}
.sidebar-menu :deep(.el-menu-item.is-active) {
  background: rgba(255, 255, 255, 0.08);
  font-weight: 700;
}
.sidebar-menu :deep(.el-menu-item.is-active::before) {
  position: absolute;
  top: 9px;
  bottom: 9px;
  left: -12px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: var(--tk-side-active);
  content: '';
}
.sidebar-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 20px 18px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}
.sidebar-foot .version {
  margin-left: auto;
  color: var(--tk-side-muted);
  font-family: var(--tk-font-num);
  font-size: 11px;
}
@media (max-height: 650px) {
  .sidebar-art {
    height: 120px;
  }
}

/* ---------------- 主区域 ---------------- */
.workspace {
  display: flex;
  min-width: 0;
  min-height: 100dvh;
  flex-direction: column;
  background: var(--tk-bg);
}
main {
  min-width: 0;
  padding: 26px 32px 40px;
}
.mobile-topbar,
.mobile-nav {
  display: none;
}

@media (max-width: 1120px) {
  .app-frame {
    grid-template-columns: 208px minmax(0, 1fr);
  }
  main {
    padding-inline: 22px;
  }
}

@media (max-width: 820px) {
  .app-frame {
    display: block;
  }
  .sidebar {
    display: none;
  }
  .mobile-topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px 6px;
    background: var(--tk-sidebar-bg);
  }
  .mobile-brand {
    display: flex;
    align-items: baseline;
    gap: 10px;
    color: var(--tk-side-text);
    font-size: 11px;
    letter-spacing: 0.14em;
  }
  .mobile-brand b {
    color: #fff;
    font-family: var(--tk-font-num);
    font-size: 20px;
    letter-spacing: 0.3em;
  }
  .mobile-tools {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .mobile-nav {
    --el-menu-bg-color: var(--tk-sidebar-bg);
    --el-menu-text-color: var(--tk-side-text);
    --el-menu-hover-bg-color: rgba(255, 255, 255, 0.05);
    --el-menu-hover-text-color: #fff;
    --el-menu-active-color: #fff;
    --el-menu-horizontal-height: 40px;
    display: flex;
    overflow-x: auto;
    gap: 2px;
    padding: 4px 8px 10px;
    border: 0;
    scrollbar-width: none;
  }
  .mobile-nav::-webkit-scrollbar {
    display: none;
  }
  .mobile-nav :deep(.el-menu-item) {
    flex: 1 0 auto;
    justify-content: center;
    height: 38px;
    padding: 0 12px;
    border: 0;
    border-radius: 9px;
    font-size: 13px;
  }
  .mobile-nav :deep(.el-menu-item.is-active) {
    background: rgba(255, 255, 255, 0.12);
    border: 0;
    font-weight: 700;
  }
  main {
    padding: 18px 14px 26px;
  }
}
</style>
