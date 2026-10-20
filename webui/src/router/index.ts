import { createRouter, createWebHashHistory } from 'vue-router'
import { useAuth } from '@/composables/useAuth'

export type RouteAccent = 'navy' | 'coral'

declare module 'vue-router' {
  interface RouteMeta {
    /** 登录前即可访问（不触发会话检查跳转）。 */
    public?: boolean
    /** 页头的章节编号、标题和英文副标题。 */
    number?: string
    title?: string
    en?: string
    accent?: RouteAccent
    /** 侧栏导航文案；缺省的路由不出现在导航里。 */
    nav?: { label: string; en: string; group: 'control' | 'workspace' }
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
            number: '01',
            title: '运行总览',
            en: 'RUNTIME OVERVIEW',
            accent: 'navy',
            nav: { label: '总览', en: 'OVERVIEW', group: 'control' },
          },
        },
        {
          path: 'accounts',
          name: 'accounts',
          component: () => import('@/views/AccountsView.vue'),
          meta: {
            number: '02',
            title: '账户与响应',
            en: 'ACCOUNTS & RESPONSE',
            accent: 'coral',
            nav: { label: '账户', en: 'ACCOUNTS', group: 'control' },
          },
        },
        {
          path: 'plugins',
          name: 'plugins',
          component: () => import('@/views/PluginsView.vue'),
          meta: {
            number: '03',
            title: '插件详情',
            en: 'PLUGIN DETAILS',
            accent: 'navy',
            nav: { label: '插件', en: 'PLUGINS', group: 'control' },
          },
        },
        {
          path: 'switches',
          name: 'switches',
          component: () => import('@/views/SwitchesView.vue'),
          meta: {
            number: '04',
            title: '功能开关',
            en: 'FEATURE FLAGS',
            accent: 'coral',
            nav: { label: '开关', en: 'FEATURE FLAGS', group: 'control' },
          },
        },
        {
          path: 'logs',
          name: 'logs',
          component: () => import('@/views/LogsView.vue'),
          meta: {
            number: '05',
            title: '运行日志',
            en: 'LOGS',
            accent: 'navy',
            nav: { label: '日志', en: 'LOGS', group: 'workspace' },
          },
        },
        {
          path: 'settings',
          name: 'settings',
          component: () => import('@/views/SettingsView.vue'),
          meta: {
            number: '06',
            title: '设置',
            en: 'SETTINGS',
            accent: 'navy',
            nav: { label: '设置', en: 'SETTINGS', group: 'workspace' },
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
