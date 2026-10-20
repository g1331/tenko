<script setup lang="ts">
import { nextTick, reactive, ref } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { api, ApiError, type AccountUpdateBody } from '@/api'
import PagePanel from '@/components/PagePanel.vue'
import { timeText } from '@/composables/format'
import { useAuth } from '@/composables/useAuth'

const emit = defineEmits<{ updated: [] }>()

const { state, setUsername } = useAuth()

const USERNAME = /^[A-Za-z0-9_.-]{1,32}$/
const MIN_PASSWORD = 8

const formRef = ref<FormInstance>()
const form = reactive({
  current_password: '',
  username: state.username,
  new_password: '',
  confirm_password: '',
})
// 后端判定的当前密码错误，作为该字段的校验信息显示（不是登录失效，不会退出登录）。
const currentError = ref('')
const saving = ref(false)

const rules: FormRules = {
  current_password: [{ required: true, message: '请输入当前密码', trigger: 'blur' }],
  username: [
    {
      pattern: USERNAME,
      message: '用户名需为 1-32 位字母、数字、下划线、点或连字符',
      trigger: 'blur',
    },
  ],
  new_password: [
    {
      validator: (_rule, value: string, done) =>
        done(
          value && value.length < MIN_PASSWORD
            ? `新密码至少需要 ${MIN_PASSWORD} 个字符`
            : undefined,
        ),
      trigger: 'blur',
    },
  ],
  confirm_password: [
    {
      validator: (_rule, value: string, done) =>
        done(value !== form.new_password ? '两次输入的新密码不一致' : undefined),
      trigger: 'blur',
    },
  ],
}

function retryText(seconds: number | null) {
  const total = seconds ?? 60
  const rest = total % 60
  return total >= 60 ? `${Math.floor(total / 60)} 分${rest ? ` ${rest} 秒` : ''}` : `${total} 秒`
}

async function submit() {
  if (!(await formRef.value?.validate().catch(() => false))) return
  const body: AccountUpdateBody = { current_password: form.current_password }
  if (form.username && form.username !== state.username) body.username = form.username
  if (form.new_password) body.new_password = form.new_password
  if (!body.username && !body.new_password) {
    ElMessage.warning('没有需要保存的更改：请修改用户名或填写新密码')
    return
  }
  currentError.value = ''
  saving.value = true
  try {
    const result = await api.auth.updateAccount(body)
    setUsername(result.username)
    form.username = result.username
    form.current_password = form.new_password = form.confirm_password = ''
    formRef.value?.clearValidate()
    ElMessage.success(
      body.new_password ? '账户已更新，新密码已生效，其他设备已退出登录' : '用户名已更新',
    )
    emit('updated')
  } catch (error) {
    if (!(error instanceof ApiError)) throw error
    if (error.status === 401 && error.code === 'invalid_credentials') {
      await nextTick()
      currentError.value = '当前密码不正确'
    } else if (error.status === 429) {
      ElMessage.error(`尝试次数过多，请在 ${retryText(error.retryAfter)} 后重试`)
    } else {
      ElMessage.error(error.message)
    }
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <PagePanel title="账户信息" accent="coral">
    <div class="identity">
      <el-avatar :size="38" class="avatar">{{
        (state.username || '?').slice(0, 1).toUpperCase()
      }}</el-avatar>
      <div>
        <strong>{{ state.username || '—' }}</strong>
        <small>
          {{ state.expiresAt ? `本次登录到期 ${timeText(state.expiresAt)}` : '面板管理员' }}
        </small>
      </div>
    </div>

    <p class="hint">修改用户名或密码都需要先验证当前密码。</p>
    <el-form
      ref="formRef"
      :model="form"
      :rules="rules"
      label-position="top"
      autocomplete="off"
      @submit.prevent="submit"
    >
      <el-form-item label="当前密码" prop="current_password" :error="currentError">
        <el-input
          v-model="form.current_password"
          type="password"
          show-password
          autocomplete="current-password"
          @input="currentError = ''"
        />
      </el-form-item>
      <el-form-item label="用户名" prop="username">
        <el-input v-model="form.username" autocomplete="username" spellcheck="false" />
      </el-form-item>
      <div class="pair">
        <el-form-item label="新密码" prop="new_password">
          <el-input
            v-model="form.new_password"
            type="password"
            show-password
            autocomplete="new-password"
            placeholder="留空则不修改密码"
          />
        </el-form-item>
        <el-form-item label="确认新密码" prop="confirm_password">
          <el-input
            v-model="form.confirm_password"
            type="password"
            show-password
            autocomplete="new-password"
          />
        </el-form-item>
      </div>
      <el-button type="primary" native-type="submit" :loading="saving">保存账户变更</el-button>
    </el-form>
  </PagePanel>
</template>

<style scoped>
.el-form-item {
  margin-bottom: 24px;
}
.identity {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
  padding: 12px 14px;
  border: 1px solid var(--tk-line);
  border-radius: 4px;
  background: var(--tk-surface-2);
}
.identity > div {
  display: flex;
  min-width: 0;
  flex-direction: column;
}
.identity strong {
  color: var(--tk-heading);
  overflow-wrap: anywhere;
}
.identity small,
.hint {
  color: var(--tk-muted);
  font-size: 12px;
}
.avatar {
  flex: none;
  background: var(--tk-navy-solid);
  color: var(--tk-on-navy);
  font-weight: 700;
}
.hint {
  margin: 0 0 12px;
}
.pair {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 14px;
}
@media (max-width: 640px) {
  .pair {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
