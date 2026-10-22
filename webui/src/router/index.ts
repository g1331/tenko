import { createRouter, createWebHashHistory } from 'vue-router'
import { useAuth } from '@/composables/useAuth'

declare module 'vue-router' {
  interface RouteMeta {
    /** 登录前即可访问（不触发会话检查跳转）。 */
    public?: boolean
    /** 页头的标题和副标题。 */
    title?: string
    subtitle?: string
    /** 侧栏导航文案（short 用于移动端横向导航）；缺省的路由不出现在导航里。 */
    nav?: { label: string; short: string; group: '管理' | '运维' }
  }
}

// 页面按需加载；页面文件固定为 src/views/<Name>View.vue，由各页面各自维护。
export const router = createRouter({
  // 哈希路由：后端只需要提供一个入口页。
  history: createWebHashHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true, title: '登录' },
    },
    {
      path: '/',
      component: () => import('@/layouts/AppLayout.vue'),
      redirect: { name: 'overview' },
      children: [
        {
          path: 'overview',
          name: 'overview',
          component: () => import('@/views/OverviewView.vue'),
          meta: {
            title: '运行总览',
            nav: { label: '总览', short: '总览', group: '管理' },
          },
        },
        {
          path: 'inbox',
          name: 'inbox',
          component: () => import('@/views/InboxView.vue'),
          meta: {
            title: '待处理',
            subtitle: '需要你决定的好友申请和群邀请',
            nav: { label: '待处理', short: '待处理', group: '管理' },
          },
        },
        {
          path: 'chat',
          name: 'chat',
          component: () => import('@/views/ChatView.vue'),
          meta: {
            title: '聊天',
            subtitle: '以 bot 账号的身份查看和发送群聊、私聊消息',
            nav: { label: '聊天', short: '聊天', group: '管理' },
          },
        },
        {
          path: 'accounts',
          name: 'accounts',
          component: () => import('@/views/AccountsView.vue'),
          meta: {
            title: '账户与响应',
            subtitle: '账号接入、绑定群和各群的响应策略',
            nav: { label: '账户与响应', short: '账户', group: '管理' },
          },
        },
        {
          path: 'plugins',
          name: 'plugins',
          component: () => import('@/views/PluginsView.vue'),
          meta: {
            title: '插件',
            subtitle: '已安装插件的状态、配置和插件市场',
            nav: { label: '插件', short: '插件', group: '管理' },
          },
        },
        {
          path: 'plugins/:plugin',
          name: 'plugin-detail',
          component: () => import('@/views/PluginDetailView.vue'),
          meta: { title: '插件详情' },
        },
        {
          path: 'switches',
          name: 'switches',
          component: () => import('@/views/SwitchesView.vue'),
          meta: {
            title: '功能开关',
            subtitle: '按群或按插件管理功能的开启与关闭',
            nav: { label: '功能开关', short: '开关', group: '管理' },
          },
        },
        {
          path: 'upgrade',
          name: 'upgrade',
          component: () => import('@/views/UpgradeView.vue'),
          meta: {
            title: '版本与升级',
            subtitle: '检查更新、升级和回滚 Tenko 宿主',
            nav: { label: '版本与升级', short: '升级', group: '运维' },
          },
        },
        {
          path: 'logs',
          name: 'logs',
          component: () => import('@/views/LogsView.vue'),
          meta: {
            title: '运行日志',
            subtitle: 'Tenko 的实时运行日志',
            nav: { label: '日志', short: '日志', group: '运维' },
          },
        },
        {
          path: 'sandbox',
          name: 'sandbox',
          component: () => import('@/views/SandboxView.vue'),
          meta: {
            title: '命令沙盒',
            subtitle: '扮演群友和 bot 对话，测试命令，消息不经过真实协议',
            nav: { label: '命令沙盒', short: '沙盒', group: '运维' },
          },
        },
        {
          path: 'settings',
          name: 'settings',
          component: () => import('@/views/SettingsView.vue'),
          meta: {
            title: '设置',
            subtitle: '面板账户、运行参数和会话管理',
            nav: { label: '设置', short: '设置', group: '运维' },
          },
        },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: { name: 'overview' } },
  ],
  scrollBehavior: (to, from) => (to.path === from.path ? undefined : { top: 0 }),
})

router.beforeEach(async (to) => {
  const auth = useAuth()
  await auth.ensureLoaded()
  if (to.meta.public) {
    return auth.isAuthenticated.value ? { name: 'overview' } : true
  }
  if (!auth.isAuthenticated.value) {
    const redirect = to.fullPath === '/' ? undefined : to.fullPath
    return { name: 'login', query: redirect ? { redirect } : undefined }
  }
  return true
})

router.afterEach((to) => {
  document.title = `Tenko WebUI · ${to.meta.title ?? '管理面板'}`
})

export default router
