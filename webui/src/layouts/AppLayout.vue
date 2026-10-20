<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BrandMark from '@/components/BrandMark.vue'
import GithubLink from '@/components/GithubLink.vue'
import TenshiPolaroid from '@/components/TenshiPolaroid.vue'
import ThemeSwitch from '@/components/ThemeSwitch.vue'
import AppHeader from './AppHeader.vue'
import { usePanelData, usePanelPolling } from '@/composables/usePanelData'

// 已登录区域的外壳：加载并定时刷新共享数据，卸载（登出 / 会话失效）时清空。
usePanelPolling()
const { overview, sync } = usePanelData()

const route = useRoute()
const router = useRouter()

interface NavItem {
  path: string
  number: string
  label: string
  en: string
  group: 'control' | 'workspace'
}
const navItems = computed<NavItem[]>(() =>
  router
    .getRoutes()
    .filter((item) => item.meta.nav)
    .map((item) => ({
      path: item.path,
      number: item.meta.number ?? '',
      label: item.meta.nav!.label,
      en: item.meta.nav!.en,
      group: item.meta.nav!.group,
    }))
    .sort((a, b) => a.number.localeCompare(b.number)),
)
const groups = computed(() => [
  { title: 'CONTROL DECK', items: navItems.value.filter((item) => item.group === 'control') },
  { title: 'WORKSPACE', items: navItems.value.filter((item) => item.group === 'workspace') },
])

const syncLabel = computed(
  () =>
    ({ syncing: 'API SYNCING', ok: 'API SYNCED', partial: 'API PARTIAL', offline: 'API OFFLINE' })[
      sync.value.state
    ],
)
const footer = computed(() => {
  const version = overview.data.value?.version
  return version ? `TENKO · v${version} · PRERELEASE` : ''
})
</script>

<template>
  <div class="app-frame">
    <aside class="sidebar" aria-label="Tenko 功能导航">
      <BrandMark class="sidebar-brand" />
      <div class="sidebar-portrait"><TenshiPolaroid /></div>

      <el-menu class="sidebar-menu" :default-active="route.path" router aria-label="功能分类">
        <el-menu-item-group v-for="group in groups" :key="group.title">
          <template #title>{{ group.title }}</template>
          <el-menu-item v-for="item in group.items" :key="item.path" :index="item.path">
            <span class="nav-number">{{ item.number }}</span>
            <span class="nav-copy">
              {{ item.label }}<span class="nav-en">{{ item.en }}</span>
            </span>
          </el-menu-item>
        </el-menu-item-group>
      </el-menu>

      <div class="sidebar-foot">
        <div class="sidebar-tools"><ThemeSwitch /><GithubLink /></div>
        TENKO WEBUI · <strong>{{ syncLabel }}</strong>
      </div>
    </aside>

    <section class="workspace">
      <header class="mobile-topbar">
        <BrandMark :caption="false" />
        <div class="sidebar-tools"><ThemeSwitch /><GithubLink /></div>
      </header>
      <el-menu
        class="mobile-nav"
        mode="horizontal"
        :ellipsis="false"
        :default-active="route.path"
        router
        aria-label="移动端功能分类"
      >
        <el-menu-item v-for="item in navItems" :key="item.path" :index="item.path">
          <span class="nav-number">{{ item.number }}</span>
          <span class="nav-label">{{ item.label }}</span>
        </el-menu-item>
      </el-menu>

      <main>
        <AppHeader />
        <router-view v-slot="{ Component }">
          <component :is="Component" :key="route.name" />
        </router-view>
      </main>
      <footer>{{ footer }}</footer>
    </section>
  </div>
</template>

<style scoped>
.app-frame {
  display: grid;
  width: 100%;
  min-height: 100vh;
  grid-template-columns: 232px minmax(0, 1fr);
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
  padding: 20px 14px 16px;
  background: var(--tk-sidebar-bg);
  color: #f5f1e8;
}
.sidebar > * {
  flex-shrink: 0;
}
.sidebar-brand {
  padding: 2px 10px 18px;
}
.sidebar-portrait {
  display: flex;
  justify-content: center;
  margin: 0 8px 20px;
  padding: 12px 8px;
  border: 1px solid rgba(245, 241, 232, 0.13);
  background: rgba(245, 241, 232, 0.05);
}
.sidebar-menu {
  --el-menu-bg-color: transparent;
  --el-menu-text-color: #c5d5e4;
  --el-menu-hover-bg-color: rgba(245, 241, 232, 0.08);
  --el-menu-hover-text-color: #f5f1e8;
  --el-menu-active-color: #2d4a6b;
  --el-menu-item-height: 50px;
  flex: 1 1 auto;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  border: 0;
}
.sidebar-menu :deep(.el-menu-item-group__title) {
  padding: 0 10px 6px;
  color: rgba(245, 241, 232, 0.5);
  font-size: 10px;
  font-style: italic;
  letter-spacing: 2px;
}
.sidebar-menu :deep(.el-menu-item-group:not(:first-child) .el-menu-item-group__title) {
  padding-top: 16px;
}
.sidebar-menu :deep(.el-menu-item) {
  margin-bottom: 3px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: 3px;
  gap: 10px;
}
.sidebar-menu :deep(.el-menu-item.is-active) {
  border-color: rgba(245, 241, 232, 0.34);
  background: #f5f1e8;
  color: #2d4a6b;
  box-shadow: 4px 4px 0 rgba(26, 26, 30, 0.16);
}
.nav-number {
  width: 22px;
  color: var(--tk-gold);
  font-family: Georgia, 'Times New Roman', serif;
  font-size: 12px;
  font-weight: 700;
}
.is-active .nav-number {
  color: #c4574a;
}
.nav-copy {
  flex: 1;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 2px;
  line-height: 1.3;
}
.nav-en {
  display: block;
  margin-top: 1px;
  font-size: 9px;
  font-style: italic;
  font-weight: 400;
  letter-spacing: 1.5px;
  opacity: 0.66;
}
.sidebar-foot {
  margin-top: auto;
  padding: 14px 10px 0;
  border-top: 1px solid rgba(245, 241, 232, 0.12);
  color: rgba(245, 241, 232, 0.55);
  font-size: 10px;
  letter-spacing: 1.5px;
  line-height: 1.8;
}
.sidebar-foot strong {
  color: var(--tk-gold);
  font-weight: 600;
}
.sidebar-tools {
  display: flex;
  align-items: center;
  gap: 12px;
}
.sidebar-foot .sidebar-tools {
  margin-bottom: 10px;
}
@media (max-height: 650px) {
  .sidebar-portrait {
    display: none;
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
  padding: 22px 28px 30px;
}
footer {
  margin-top: auto;
  padding: 16px 0 14px;
  color: var(--tk-muted);
  font-size: 10px;
  letter-spacing: 3px;
  text-align: center;
}
footer:empty {
  padding: 0;
}
.mobile-topbar,
.mobile-nav {
  display: none;
}

@media (max-width: 1120px) {
  .app-frame {
    grid-template-columns: 204px minmax(0, 1fr);
  }
  main {
    padding-inline: 20px;
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
  .mobile-nav {
    --el-menu-bg-color: var(--tk-sidebar-bg);
    --el-menu-text-color: #c5d5e4;
    --el-menu-hover-bg-color: rgba(245, 241, 232, 0.08);
    --el-menu-hover-text-color: #f5f1e8;
    --el-menu-active-color: #2d4a6b;
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
    gap: 3px;
    height: 38px;
    padding: 0 8px;
    border: 0;
    border-radius: 3px;
    color: #c5d5e4;
  }
  .mobile-nav :deep(.el-menu-item.is-active) {
    background: #f5f1e8;
    color: #2d4a6b;
    border: 0;
  }
  .mobile-nav .nav-number {
    width: auto;
    font-size: 11px;
  }
  .nav-label {
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.5px;
  }
  main {
    padding: 16px 14px 22px;
  }
}
@media (max-width: 520px) {
  footer {
    letter-spacing: 1.5px;
  }
}
</style>
