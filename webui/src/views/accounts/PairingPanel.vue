<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { api, type PairingInfo } from '@/api'
import PagePanel from '@/components/PagePanel.vue'
import { confirmAction } from '@/composables/confirm'

// 配对信息只在打开页面时读取一次，令牌仅保存在本页内存，离开页面即丢弃。
const info = shallowRef<PairingInfo | null>(null)
const loading = ref(true)
const loadError = ref('')
const open = ref<string[]>([])
const url = ref('')
const token = ref<string | null>(null)
const busy = ref<'' | 'reveal' | 'rotate'>('')

const placeholder = computed(() => `ws://协议端可达的主机:端口${info.value?.path ?? ''}`)

async function load() {
  loading.value = true
  try {
    info.value = await api.manage.pairing()
    url.value = info.value.requires_host ? '' : info.value.url
    loadError.value = ''
  } catch (error) {
    loadError.value = (error as Error).message
  } finally {
    loading.value = false
  }
}
onMounted(load)
onBeforeUnmount(() => {
  token.value = null
})

async function run(action: 'reveal' | 'rotate') {
  if (action === 'rotate') {
    const ok = await confirmAction(
      '将轮换所有协议端共用的 OneBot token，并重写 TOML 配置（不保留注释）。新连接立即使用新 token，现有连接保持。请准备同步更新各协议端。',
      '轮换连接令牌',
    )
    if (!ok) return
  }
  busy.value = action
  try {
    const data = await api.manage.pairingAction(
      action === 'rotate' ? { action: 'rotate', confirm: true } : { action: 'reveal' },
    )
    info.value = data
    token.value = data.access_token ?? ''
    if (action === 'rotate') ElMessage.success('新令牌已保存，请复制并更新协议端')
    else if (!token.value) ElMessage.info('当前没有配置连接令牌')
    else ElMessage.success('凭据已载入，仅保留在本页内存')
  } catch (error) {
    ElMessage.error(
      `${action === 'rotate' ? '轮换令牌' : '读取凭据'}失败：${(error as Error).message}`,
    )
  } finally {
    busy.value = ''
  }
}

async function copyFields() {
  try {
    const parsed = new URL(url.value.trim())
    const badHost = ['0.0.0.0', '[::]'].includes(parsed.hostname)
    if (!['ws:', 'wss:'].includes(parsed.protocol) || badHost || parsed.username || parsed.password)
      throw new Error('请填写有效且可达的 ws:// 或 wss:// 地址')
    if (!navigator.clipboard)
      throw new Error('当前地址不支持自动复制，请显示明文后手动复制，或使用 HTTPS / 本机访问')
    await navigator.clipboard.writeText(
      JSON.stringify({ url: parsed.href, token: token.value }, null, 2),
    )
    ElMessage.success('连接字段已复制，请粘贴到可信的协议端')
  } catch (error) {
    ElMessage.error(
      error instanceof TypeError
        ? '请填写有效且可达的 ws:// 或 wss:// 地址'
        : (error as Error).message,
    )
  }
}
</script>

<template>
  <PagePanel accent="gold" class="pairing">
    <el-collapse v-model="open" class="pairing-collapse">
      <el-collapse-item name="pairing" title="添加账户 · 配对接入">
        <el-skeleton v-if="loading" :rows="2" animated />
        <el-empty
          v-else-if="!info"
          :image-size="48"
          :description="`配对信息暂不可用：${loadError}`"
        >
          <el-button size="small" @click="load">重试</el-button>
        </el-empty>
        <div v-else class="pairing-body">
          <p class="hint">
            在协议端添加 WebSocket 客户端，填入下方地址和令牌。连接成功后，账户会自动出现在列表中。
          </p>
          <el-alert
            v-if="!info.token_configured"
            type="info"
            :closable="false"
            show-icon
            class="token-hint"
            title="当前没有配置连接令牌，协议端无需令牌即可连接。"
          />
          <el-form label-position="top" class="pairing-form" @submit.prevent>
            <el-form-item label="连接地址">
              <el-input
                v-model="url"
                :placeholder="placeholder"
                autocomplete="off"
                aria-label="连接地址"
              />
              <div v-if="info.requires_host" class="hint">
                当前监听所有地址，请填写协议端实际可达的地址；不要填写 0.0.0.0 或 ::。
              </div>
            </el-form-item>
            <el-form-item v-if="token !== null && token !== ''" label="OneBot Token">
              <el-input
                :model-value="token"
                type="password"
                show-password
                readonly
                autocomplete="off"
                aria-label="OneBot Token"
              />
            </el-form-item>
          </el-form>
          <div class="actions">
            <el-button :loading="busy === 'reveal'" :disabled="!!busy" @click="run('reveal')">
              显示连接凭据
            </el-button>
            <el-button
              type="danger"
              plain
              :loading="busy === 'rotate'"
              :disabled="!info.can_rotate || !!busy"
              @click="run('rotate')"
            >
              轮换连接令牌
            </el-button>
            <template v-if="token">
              <el-button @click="copyFields">复制连接字段</el-button>
              <el-button @click="token = null">隐藏凭据</el-button>
            </template>
          </div>
          <p class="hint">
            轮换影响使用该令牌的全部协议端；新连接立即使用新令牌，已有连接保持。请同步更新各协议端配置。
          </p>
        </div>
      </el-collapse-item>
    </el-collapse>
  </PagePanel>
</template>

<style scoped>
.pairing {
  --el-card-padding: 0 20px;
  margin-bottom: 16px;
}
.pairing-collapse {
  --el-collapse-header-height: 52px;
  --el-collapse-border-color: transparent;
  --el-collapse-header-bg-color: transparent;
  --el-collapse-content-bg-color: transparent;
  border: 0;
}
.pairing-collapse :deep(.el-collapse-item__header) {
  color: var(--tk-heading);
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 1px;
}
.pairing-collapse :deep(.el-collapse-item__wrap) {
  border-bottom: 0;
}
.pairing-collapse :deep(.el-collapse-item__content) {
  padding-bottom: 18px;
}
.pairing-body {
  max-width: 760px;
}
.hint {
  margin: 0 0 12px;
  color: var(--tk-muted);
  font-size: 12px;
  line-height: 1.65;
}
.token-hint {
  margin-bottom: 12px;
}
.pairing-form :deep(.el-form-item__label) {
  color: var(--tk-text);
  font-weight: 600;
}
.pairing-form .hint {
  margin: 6px 0 0;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.actions .el-button {
  margin: 0;
}
</style>
