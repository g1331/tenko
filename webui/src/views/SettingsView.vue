<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Search } from '@element-plus/icons-vue'
import PageContainer from '@/components/PageContainer.vue'
import { useNarrow } from '@/composables/useNarrow'
import FriendRequestSettings from './settings/FriendRequestSettings.vue'
import NotifySettings from './settings/NotifySettings.vue'
import CoreConfigSettings, { type CoreConfigIndex } from './settings/CoreConfigSettings.vue'
import AccountForm from './settings/AccountForm.vue'
import RuntimeSettings from './settings/RuntimeSettings.vue'
import SessionList from './settings/SessionList.vue'

// 设置页：左侧目录（窄屏为顶部吸附的分类条）+ 搜索。目录项对应页面里的一个面板或宿主配置
// 的一个分组；搜索匹配目录名、关键词和宿主配置的字段名与说明，不匹配的面板和字段隐藏。

interface NavItem {
  id: string
  label: string
  /** 搜索用的关键词：面板里的字段、选项和常见说法。 */
  keys: string
}
interface NavSection {
  title: string
  items: NavItem[]
}

const route = useRoute()
const router = useRouter()
const narrow = useNarrow(1100)
const sessions = ref<InstanceType<typeof SessionList> | null>(null)
const query = ref('')
const coreIndex = ref<CoreConfigIndex[]>([])

const sections = computed<NavSection[]>(() => [
  {
    title: '运行设置',
    items: [
      { id: 'set-policy', label: '功能默认策略', keys: '默认启用 默认关闭 新群 未设置的功能 开关' },
      { id: 'set-notify', label: '通知', keys: '通知 Master 私聊 通知群 发送账号 告警' },
      { id: 'set-friend', label: '好友申请', keys: '好友 申请 自动同意 仅管理员 所有人 处理策略' },
    ],
  },
  {
    title: '宿主配置',
    items: coreIndex.value.map((group) => ({
      id: `set-core-${group.key}`,
      label: group.title,
      keys: group.text,
    })),
  },
  {
    title: '账号与安全',
    items: [
      { id: 'account-security', label: '账户信息', keys: '用户名 密码 修改密码 账户' },
      { id: 'set-sessions', label: '登录会话', keys: '会话 设备 浏览器 注销 登录 IP' },
    ],
  },
])

const terms = computed(() => query.value.trim().toLowerCase().split(/\s+/).filter(Boolean))
const matches = (item: NavItem, section: NavSection) => {
  const text = `${section.title} ${item.label} ${item.keys}`.toLowerCase()
  return terms.value.every((term) => text.includes(term))
}
/** 搜索时保留的目录项 ID。 */
const visible = computed(
  () =>
    new Set(
      sections.value.flatMap((section) =>
        section.items.filter((item) => matches(item, section)).map((item) => item.id),
      ),
    ),
)
const shown = (id: string) => !terms.value.length || visible.value.has(id)
const coreShown = computed(() => coreIndex.value.some((group) => shown(`set-core-${group.key}`)))
const nothing = computed(() => !!terms.value.length && !visible.value.size)

// ---------------------------------------------------------------- 目录跟随与跳转

const active = ref('set-policy')
let frame = 0
function track() {
  cancelAnimationFrame(frame)
  frame = requestAnimationFrame(() => {
    const offset = narrow.value ? 140 : 96
    let current = ''
    for (const section of sections.value) {
      for (const item of section.items) {
        const el = document.getElementById(item.id)
        if (el && el.offsetParent !== null && el.getBoundingClientRect().top <= offset) {
          current = item.id
        }
      }
    }
    active.value = current || visible.value.values().next().value || 'set-policy'
  })
}

function jump(id: string) {
  active.value = id
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  void router.replace({ query: { ...route.query, section: id } })
}

// 顶栏用户菜单以 ?section=account-security 跳转到这里；等路由自身的回顶滚动结束后再定位。
onMounted(async () => {
  window.addEventListener('scroll', track, { passive: true })
  const wanted = route.query.section
  if (typeof wanted !== 'string') return
  await nextTick()
  requestAnimationFrame(() => document.getElementById(wanted)?.scrollIntoView({ block: 'start' }))
})
onBeforeUnmount(() => {
  window.removeEventListener('scroll', track)
  cancelAnimationFrame(frame)
})
watch(query, () => nextTick(track))
</script>

<template>
  <PageContainer>
    <div class="settings">
      <aside class="nav" :class="{ bar: narrow }" aria-label="设置目录">
        <el-input
          v-model="query"
          class="search"
          clearable
          placeholder="搜索设置项"
          aria-label="搜索设置"
          :prefix-icon="Search"
        />
        <nav class="toc">
          <template v-for="section in sections" :key="section.title">
            <div
              v-if="section.items.some((item) => shown(item.id))"
              class="toc-group"
              role="group"
              :aria-label="section.title"
            >
              <span class="toc-title">{{ section.title }}</span>
              <template v-for="item in section.items" :key="item.id">
                <button
                  v-if="shown(item.id)"
                  type="button"
                  class="toc-item"
                  :class="{ on: active === item.id }"
                  :aria-current="active === item.id ? 'location' : undefined"
                  @click="jump(item.id)"
                >
                  {{ item.label }}
                </button>
              </template>
            </div>
          </template>
        </nav>
      </aside>

      <div class="content">
        <el-empty
          v-if="nothing"
          :image-size="64"
          :description="`没有找到与“${query.trim()}”相关的设置`"
        >
          <el-button size="small" @click="query = ''">清除搜索</el-button>
        </el-empty>

        <section
          v-show="shown('set-policy') || shown('set-notify') || shown('set-friend')"
          class="section"
        >
          <h2>运行设置</h2>
          <div class="stack">
            <div v-show="shown('set-policy')" id="set-policy" class="anchor">
              <RuntimeSettings />
            </div>
            <div v-show="shown('set-notify')" id="set-notify" class="anchor">
              <NotifySettings />
            </div>
            <div v-show="shown('set-friend')" id="set-friend" class="anchor">
              <FriendRequestSettings />
            </div>
          </div>
        </section>

        <section v-show="coreShown || !coreIndex.length" class="section">
          <h2>宿主配置</h2>
          <CoreConfigSettings :query="query" @index="coreIndex = $event" />
        </section>

        <section v-show="shown('account-security') || shown('set-sessions')" class="section">
          <h2>账号与安全</h2>
          <div class="grid">
            <div v-show="shown('account-security')" id="account-security" class="anchor">
              <AccountForm @updated="sessions?.reload()" />
            </div>
            <div v-show="shown('set-sessions')" id="set-sessions" class="anchor">
              <SessionList ref="sessions" />
            </div>
          </div>
        </section>
      </div>
    </div>
  </PageContainer>
</template>

<style scoped>
.settings {
  display: grid;
  grid-template-columns: 208px minmax(0, 1fr);
  align-items: start;
  gap: 24px;
}
.nav {
  position: sticky;
  top: 16px;
  display: grid;
  gap: 14px;
}
.toc {
  display: grid;
  gap: 14px;
}
.toc-group {
  display: grid;
  gap: 2px;
}
.toc-title {
  padding: 0 10px 4px;
  color: var(--tk-muted);
  font-size: 11px;
  letter-spacing: 0.14em;
}
.toc-item {
  position: relative;
  padding: 7px 10px;
  border: 0;
  border-radius: 8px;
  background: none;
  color: var(--tk-text);
  font: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}
.toc-item:hover {
  background: var(--tk-surface-2);
}
.toc-item.on {
  background: var(--tk-surface-2);
  color: var(--tk-heading);
  font-weight: 700;
}
.toc-item.on::before {
  position: absolute;
  top: 8px;
  bottom: 8px;
  left: 0;
  width: 3px;
  border-radius: 2px;
  background: var(--el-color-primary);
  content: '';
}
.toc-item:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: -2px;
}
.section {
  margin-bottom: 28px;
}
.section h2 {
  margin: 0 0 12px;
  color: var(--tk-heading);
  font-size: 16px;
  letter-spacing: 0.04em;
}
.anchor,
.content :deep(.core-group) {
  scroll-margin-top: 16px;
}
.stack {
  display: grid;
  gap: 16px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: start;
  gap: 16px;
}

/* 窄屏：目录变成吸附在顶部的搜索框 + 横向分类条。 */
@media (max-width: 1100px) {
  .settings {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }
  .nav.bar {
    top: 0;
    z-index: 5;
    gap: 8px;
    padding: 10px 0 8px;
    border-bottom: 1px solid var(--tk-line);
    background: var(--tk-bg);
    /* 左右各补一条页面底色，盖住两侧页边距里滚过的内容 */
    box-shadow:
      -24px 0 0 var(--tk-bg),
      24px 0 0 var(--tk-bg);
  }
  .bar .toc {
    display: flex;
    gap: 6px;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .bar .toc::-webkit-scrollbar {
    display: none;
  }
  .bar .toc-group {
    display: flex;
    gap: 6px;
  }
  .bar .toc-title {
    display: none;
  }
  .bar .toc-item {
    flex: none;
    padding: 5px 12px;
    border: 1px solid var(--tk-line);
    border-radius: 999px;
    white-space: nowrap;
  }
  .bar .toc-item.on {
    border-color: var(--el-color-primary);
    background: var(--el-color-primary-light-9);
    color: var(--el-color-primary);
  }
  .bar .toc-item.on::before {
    display: none;
  }
  .anchor,
  .content :deep(.core-group) {
    scroll-margin-top: 112px;
  }
  .grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
