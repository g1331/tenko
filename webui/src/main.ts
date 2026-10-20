import { createApp } from 'vue'
import 'element-plus/theme-chalk/dark/css-vars.css'
import './styles/theme.css'
import App from './App.vue'
import router from './router'
import { installAuthGuard } from './composables/useAuth'
import { installTheme } from './composables/useTheme'

installTheme()
// 任一接口返回 401（会话失效）：清除登录态并回到登录页，登录后回到原页面。
installAuthGuard(() => {
  ElMessage.warning('登录已失效，请重新登录')
  const redirect = router.currentRoute.value.fullPath
  void router.replace({ name: 'login', query: redirect === '/' ? undefined : { redirect } })
})

createApp(App).use(router).mount('#app')
