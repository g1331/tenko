<script setup lang="ts">
import { ref, watch } from 'vue'
import { api, type CapabilityOverrides, type ManagedAccount } from '@/api'
import QqAvatar from '@/components/QqAvatar.vue'
import { platformText } from '@/composables/format'
import { accountName, CAPABILITY_LABELS } from './helpers'

// 账户偏好：别名与能力覆盖。保存成功后关闭并通知页面刷新；失败以 toast 提示并保持打开。
const props = defineProps<{ account: ManagedAccount | null; capabilities: string[] }>()
const visible = defineModel<boolean>({ required: true })
const emit = defineEmits<{ saved: [] }>()

type Choice = 'auto' | 'true' | 'false'
const alias = ref('')
const choices = ref<Record<string, Choice>>({})
const saving = ref(false)

watch(visible, (value) => {
  if (!value || !props.account) return
  alias.value = props.account.alias
  choices.value = Object.fromEntries(
    props.capabilities.map((name) => {
      const current = props.account!.capabilities[name]
      return [name, current === true ? 'true' : current === false ? 'false' : 'auto']
    }),
  )
})

async function save() {
  const account = props.account
  if (!account) return
  const capabilities: CapabilityOverrides = {}
  for (const [name, choice] of Object.entries(choices.value)) {
    if (choice !== 'auto') capabilities[name] = choice === 'true'
  }
  saving.value = true
  try {
    await api.manage.accountAction(account.platform, account.id, 'preferences', {
      alias: alias.value,
      capabilities,
    })
    ElMessage.success('账户偏好已保存')
    visible.value = false
    emit('saved')
  } catch (error) {
    ElMessage.error(`保存偏好失败：${(error as Error).message}`)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" title="账户偏好" width="min(560px, calc(100vw - 24px))" align-center>
    <template v-if="account">
      <p class="who">
        <QqAvatar
          :id="account.id"
          :name="accountName(account)"
          :platform="account.platform"
          :size="28"
          kind="user"
        />
        <span
          >{{ accountName(account) }} · {{ platformText(account.platform) }} ·
          <span class="tk-mono">{{ account.id }}</span></span
        >
      </p>
      <el-form label-position="top" @submit.prevent="save">
        <el-form-item label="别名">
          <el-input
            v-model="alias"
            maxlength="128"
            show-word-limit
            placeholder="留空则显示协议端用户名"
          />
        </el-form-item>
        <h4>能力覆盖</h4>
        <p class="hint">自动探测沿用协议回执；强制允许不会授予平台权限。</p>
        <div class="caps">
          <el-form-item
            v-for="name in capabilities"
            :key="name"
            :label="CAPABILITY_LABELS[name] ?? name"
          >
            <el-select v-model="choices[name]" :aria-label="CAPABILITY_LABELS[name] ?? name">
              <el-option label="自动探测" value="auto" />
              <el-option label="允许" value="true" />
              <el-option label="禁用" value="false" />
            </el-select>
          </el-form-item>
        </div>
        <p class="hint">
          绑定 {{ account.groups.length }} 个群；响应策略在“群响应策略”面板中管理。
        </p>
      </el-form>
    </template>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存偏好</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.who {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 14px;
  color: var(--tk-muted);
  font-size: 12px;
}
h4 {
  margin: 4px 0 2px;
  color: var(--tk-heading);
  font-size: 13px;
  letter-spacing: 0.04em;
}
.hint {
  margin: 0 0 10px;
  color: var(--tk-muted);
  font-size: 12px;
  line-height: 1.65;
  overflow-wrap: anywhere;
}
.caps {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 16px;
}
@media (max-width: 520px) {
  .caps {
    grid-template-columns: 1fr;
  }
}
</style>
