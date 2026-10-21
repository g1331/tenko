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
