<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useTemplateRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { InputInstance } from 'element-plus'
import { ApiError } from '@/api'
import GithubLink from '@/components/GithubLink.vue'
import ThemeSwitch from '@/components/ThemeSwitch.vue'
import { useAuth } from '@/composables/useAuth'
import tenshi from '@/assets/tenshi.webp'

const auth = useAuth()
const route = useRoute()
const router = useRouter()

const username = ref('master')
const password = ref('')
const submitting = ref(false)
const error = ref('')
/** 触发限流后的剩余秒数；大于 0 时禁用提交并显示倒计时。 */
const lockLeft = ref(0)
let lockTimer = 0

const passwordInput = useTemplateRef<InputInstance>('passwordInput')

const countdown = computed(() => {
  const left = lockLeft.value
  return `${String(Math.floor(left / 60)).padStart(2, '0')}:${String(left % 60).padStart(2, '0')}`
})

function stopLock() {
  clearInterval(lockTimer)
  lockTimer = 0
  lockLeft.value = 0
}

function startLock(seconds: number) {
  stopLock()
  const until = Date.now() + Math.max(1, seconds) * 1000
  const tick = () => {
    lockLeft.value = Math.max(0, Math.ceil((until - Date.now()) / 1000))
    if (!lockLeft.value) stopLock()
  }
  tick()
  lockTimer = window.setInterval(tick, 1000)
}

/** 登录后回到被拦截的页面；只接受站内路径，避免被构造的 redirect 带走。 */
function destination() {
  const redirect = route.query.redirect
  return typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//')
    ? redirect
    : '/overview'
}

async function submit() {
  if (submitting.value || lockLeft.value) return
  const name = username.value.trim()
  if (!name || !password.value) {
    error.value = name ? '请输入密码。' : '请输入用户名。'
    return
  }
  error.value = ''
  submitting.value = true
  try {
    await auth.login(name, password.value)
    password.value = ''
    await router.replace(destination())
  } catch (reason) {
    const failure = reason instanceof ApiError ? reason : new ApiError(String(reason))
    if (failure.status === 429 || failure.code === 'too_many_attempts') {
      startLock(failure.retryAfter ?? 60)
    } else if (failure.code === 'password_not_set' || failure.status === 409) {
      auth.state.passwordSet = false
    } else if (failure.code === 'invalid_credentials' || failure.status === 401) {
      error.value = '用户名或密码不正确。'
      passwordInput.value?.select()
    } else {
      error.value = `登录失败：${failure.message}`
    }
  } finally {
    submitting.value = false
  }
}

/** 重新向后端确认状态：已设置密码则回到表单，已登录则直接进入面板。 */
async function recheck() {
  await auth.refresh()
  if (auth.isAuthenticated.value) await router.replace(destination())
  else if (!auth.state.passwordSet && !auth.state.stateError) ElMessage.info('仍未检测到面板密码')
}

onMounted(async () => {
  await nextTick()
  passwordInput.value?.focus()
})
onBeforeUnmount(stopLock)
</script>

<template>
  <section class="login-view" aria-labelledby="login-title">
    <div class="login-tools">
      <ThemeSwitch tone="page" />
      <GithubLink class="login-github" />
    </div>

    <div class="login-card">
      <div class="login-aside" :style="{ backgroundImage: `url(${tenshi})` }">
        <div class="login-brand">
          <b>TENKO</b>
          <span>比那名居天子 · 群管家</span>
        </div>
      </div>

      <div class="login-body">
        <!-- 无法读取登录状态 -->
        <template v-if="auth.state.stateError">
          <h1 id="login-title">登录管理面板</h1>
          <p class="lead error-text" role="alert">无法读取登录状态：{{ auth.state.stateError }}</p>
          <el-button class="wide" @click="recheck">重试</el-button>
        </template>

        <!-- 尚未设置面板密码 -->
        <template v-else-if="!auth.state.passwordSet">
          <h1 id="login-title">尚未设置面板密码</h1>
          <p class="lead">面板登录密码尚未设置，请用以下任一方式设置后再登录。</p>
          <ol class="setup-steps">
            <li>
              <strong>通过 QQ 私聊设置</strong>
              以 Bot Master 身份向机器人私聊发送
              <code>/面板重置密码</code>，机器人会回复新的登录密码。
            </li>
            <li>
              <strong>在服务器上设置</strong>
              在 Tenko 部署目录运行
              <code>python -m tenko webui-passwd</code>，终端会输出用户名和新密码。
              <small>默认用户名为 master。</small>
            </li>
          </ol>
          <el-button class="wide" @click="recheck">我已设置，重新检查</el-button>
        </template>

        <!-- 登录表单 -->
        <template v-else>
          <h1 id="login-title">登录管理面板</h1>
          <p class="lead">使用面板账号登录。</p>
          <el-form class="login-form" label-position="top" novalidate @submit.prevent="submit">
            <el-form-item label="用户名" for="login-username">
              <el-input
                id="login-username"
                v-model="username"
                name="username"
                autocomplete="username"
                autocapitalize="none"
                :spellcheck="false"
              />
            </el-form-item>
            <el-form-item label="密码" for="login-password">
              <el-input
                id="login-password"
                ref="passwordInput"
                v-model="password"
                name="password"
                type="password"
                show-password
                autocomplete="current-password"
              />
            </el-form-item>
            <p v-if="lockLeft" class="form-error" role="alert">
              登录尝试次数过多，请在 <b>{{ countdown }}</b> 后重试。
            </p>
            <p v-else-if="error" class="form-error" role="alert">{{ error }}</p>
            <el-button
              class="wide"
              type="primary"
              native-type="submit"
              :loading="submitting"
              :disabled="lockLeft > 0"
            >
              登录
            </el-button>
            <p class="form-help">
              忘记密码？由 Bot Master 在 QQ 私聊中发送 <code>/面板重置密码</code>，或在服务器上运行
              <code>python -m tenko webui-passwd</code>。
            </p>
          </el-form>
        </template>
      </div>
    </div>
  </section>
</template>

<style scoped>
.login-view {
  --github-color: var(--tk-muted);
  --github-hover: var(--tk-heading);
  position: relative;
  display: grid;
  min-height: 100vh;
  place-items: center;
  padding: 56px 20px 28px;
  background: var(--tk-bg);
}
.login-tools {
  position: absolute;
  top: 14px;
  right: 16px;
  display: flex;
  align-items: center;
  gap: 10px;
}
.login-card {
  display: grid;
  width: min(860px, 100%);
  grid-template-columns: 300px minmax(0, 1fr);
  overflow: hidden;
  border: 1px solid var(--tk-line);
  border-radius: 18px;
  background: var(--tk-surface);
  box-shadow:
    var(--tk-shadow),
    0 24px 60px rgba(20, 30, 60, 0.1);
}
.login-aside {
  position: relative;
  display: flex;
  min-height: 380px;
  align-items: flex-end;
  padding: 26px 28px;
  background-color: var(--tk-sidebar-bg);
  background-position: center 22%;
  background-size: cover;
}
.login-aside::after {
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, transparent 45%, var(--tk-sidebar-bg) 100%);
  content: '';
}
.login-brand {
  position: relative;
  text-shadow: 0 1px 8px rgba(8, 12, 30, 0.6);
  z-index: 1;
  display: grid;
}
.login-brand b {
  color: #fff;
  font-family: var(--tk-font-num);
  font-size: 28px;
  letter-spacing: 0.32em;
  line-height: 1.1;
}
.login-brand span {
  color: var(--tk-side-text);
  font-size: 12px;
  letter-spacing: 0.14em;
}
.login-body {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 38px 42px;
}
h1 {
  margin: 0;
  color: var(--tk-heading);
  font-size: 26px;
  letter-spacing: 0.04em;
}
.lead {
  margin: 6px 0 0;
  color: var(--tk-muted);
}
.error-text {
  color: var(--tk-danger);
}
.login-form {
  margin-top: 16px;
}
.login-form :deep(.el-form-item__label) {
  color: var(--tk-heading);
  font-weight: 600;
}
.login-form :deep(.el-input__wrapper) {
  min-height: 42px;
}
.wide {
  width: 100%;
  height: 42px;
  margin-top: 18px;
}
.login-form .wide {
  margin-top: 6px;
}
.form-error {
  margin: 4px 0 0;
  padding: 9px 12px;
  border-radius: 8px;
  background: var(--tk-danger-bg);
  color: var(--tk-danger);
}
.form-error b {
  font-variant-numeric: tabular-nums;
}
.form-help {
  margin: 12px 0 0;
  color: var(--tk-muted);
  font-size: 12px;
}
.setup-steps {
  display: grid;
  gap: 12px;
  margin: 14px 0 0;
  padding: 0;
  counter-reset: step;
  list-style: none;
}
.setup-steps li {
  position: relative;
  padding: 12px 14px 12px 46px;
  border: 1px solid var(--tk-line);
  border-radius: 10px;
  background: var(--tk-surface-2);
  counter-increment: step;
}
.setup-steps li::before {
  position: absolute;
  top: 12px;
  left: 14px;
  display: grid;
  width: 22px;
  height: 22px;
  place-items: center;
  border-radius: 50%;
  background: var(--tk-primary);
  color: var(--tk-on-primary);
  content: counter(step);
  font-family: var(--tk-font-num);
  font-size: 12px;
  font-weight: 700;
}
.setup-steps strong {
  display: block;
  color: var(--tk-heading);
}
.setup-steps code,
.form-help code {
  padding: 1px 6px;
  border-radius: 5px;
  background: var(--tk-off-bg);
  color: var(--tk-heading);
}
.setup-steps small {
  display: block;
  margin-top: 2px;
  color: var(--tk-muted);
}

@media (max-width: 700px) {
  .login-card {
    grid-template-columns: 1fr;
  }
  .login-aside {
    min-height: 120px;
    padding: 14px 20px;
  }
  .login-brand b {
    font-size: 22px;
  }
  .login-body {
    padding: 26px 22px 28px;
  }
}
</style>
