<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Delete, FullScreen, ScaleToOriginal } from '@element-plus/icons-vue'
import {
  api,
  ApiError,
  type ChatMessage,
  type ChatSendSegment,
  type SandboxChat,
  type SandboxData,
  type SandboxIdentity,
} from '@/api'
import PageContainer from '@/components/PageContainer.vue'
import { usePanelData } from '@/composables/usePanelData'
import ChatComposer, { type Mention, type ReplyTarget } from './chat/ChatComposer.vue'
import ChatMessages from './chat/ChatMessages.vue'
import { previewOf, useImmersive } from './chat/shared'

// 命令沙盒：面板扮演一个群友（或 Master）和 bot 对话。消息变成虚拟平台的标准事件，
// 走完整的事件过滤链和插件分发，但不经过真实协议；记录只在内存里，重启后清空。
const { sandboxSignal } = usePanelData()
const immersive = useImmersive()

const IDENTITY_LABELS: Record<SandboxIdentity, string> = {
  member: '群成员',
  admin: '群管理员',
  owner: '群主',
  master: 'Master',
}
const IDENTITY_KEY = 'tenko.sandbox.identity'

const chat = ref<SandboxChat>('group')
const identity = ref<SandboxIdentity>(
  (localStorage.getItem(IDENTITY_KEY) as SandboxIdentity | null) ?? 'member',
)
watch(identity, (value) => localStorage.setItem(IDENTITY_KEY, value))

const data = ref<SandboxData | null>(null)
const error = ref('')
const sending = ref(false)
const reply = ref<ReplyTarget | null>(null)
const composer = ref<InstanceType<typeof ChatComposer> | null>(null)
const list = ref<InstanceType<typeof ChatMessages> | null>(null)
/** 切换会话时递增，丢弃上一个会话的在途请求。 */
let token = 0

const errorText = (value: unknown) =>
  value instanceof ApiError ? value.message : value instanceof Error ? value.message : '请求失败'

async function load() {
  const ticket = ++token
  try {
    const value = await api.manage.sandbox(chat.value)
    if (ticket !== token) return
    data.value = value
    error.value = ''
  } catch (value) {
    if (ticket === token) error.value = errorText(value)
  }
}
load()
watch(chat, () => {
  reply.value = null
  data.value = null
  load()
})
watch(sandboxSignal, load)

const messages = computed<ChatMessage[]>(() => data.value?.messages ?? [])
const title = computed(() =>
  chat.value === 'group' ? (data.value?.group.name ?? '沙盒群') : '和 bot 私聊',
)
const mentionable = computed<Mention[]>(() =>
  chat.value === 'group' && data.value
    ? [{ id: data.value.bot.id, name: data.value.bot.name || 'bot' }]
    : [],
)
const blocked = computed(() => {
  if (error.value) return `沙盒不可用：${error.value}`
  if (!data.value) return '正在连接沙盒…'
  if (!data.value.ready) return '沙盒还没有就绪，稍后再试'
  return null
})

function replyTo(message: ChatMessage) {
  if (!message.message_id) return
  reply.value = {
    messageId: message.message_id,
    name: message.user_name || message.user_id,
    preview: previewOf(message.content),
  }
  composer.value?.focus()
}
function mentionOf(message: ChatMessage) {
  composer.value?.mention({ id: message.user_id, name: message.user_name })
}

async function send(content: ChatSendSegment[]) {
  sending.value = true
  try {
    await api.manage.sandboxSend({ chat: chat.value, identity: identity.value, content })
    composer.value?.clear()
    reply.value = null
    await load()
    list.value?.scrollToBottom()
  } catch (value) {
    ElMessage.error(errorText(value))
  } finally {
    sending.value = false
  }
}

async function clear() {
  try {
    await api.manage.sandboxClear()
    await load()
  } catch (value) {
    ElMessage.error(errorText(value))
  }
}
</script>

<template>
  <PageContainer>
    <div class="sandbox-shell" :class="{ immersive }">
      <header class="sandbox-head">
        <el-segmented
          v-model="chat"
          :options="[
            { label: '群聊', value: 'group' },
            { label: '私聊', value: 'private' },
          ]"
          aria-label="选择会话"
        />
        <div class="sandbox-title">
          <strong>{{ title }}</strong>
          <span class="muted">记录只在内存里，重启后清空</span>
        </div>
        <label class="identity">
          <span class="muted">身份</span>
          <el-select v-model="identity" size="small" aria-label="测试身份" class="identity-select">
            <el-option
              v-for="(label, value) in IDENTITY_LABELS"
              :key="value"
              :value="value"
              :label="label"
            />
          </el-select>
        </label>
        <el-button
          text
          :icon="Delete"
          title="清空记录"
          aria-label="清空记录"
          :disabled="!messages.length"
          @click="clear"
        />
        <el-button
          text
          :icon="immersive ? ScaleToOriginal : FullScreen"
          :title="immersive ? '退出全屏（Esc）' : '全屏'"
          :aria-label="immersive ? '退出全屏' : '全屏'"
          @click="immersive = !immersive"
        />
      </header>
      <section class="pane">
        <ChatMessages
          ref="list"
          :messages="messages"
          :has-more="false"
          :loading="false"
          platform="sandbox"
          :chat-type="chat"
          @reply="replyTo"
          @mention="mentionOf"
        />
        <div v-if="data && !messages.length" class="pane-empty">
          <p>发一条命令试试，例如 <code>/帮助</code></p>
          <p class="muted">
            消息会走完整的事件处理、功能开关和限流，但不经过真实协议。
            {{ chat === 'group' ? '群聊里可以切换身份测试权限。' : '' }}
          </p>
        </div>
        <ChatComposer
          ref="composer"
          :blocked="blocked"
          :sending="sending"
          :mentionable="mentionable"
          :reply="reply"
          @send="send"
          @cancel-reply="reply = null"
        />
      </section>
    </div>
  </PageContainer>
</template>

<style scoped>
.sandbox-shell {
  display: flex;
  flex-direction: column;
  height: max(460px, calc(100dvh - 150px));
  overflow: hidden;
  border: 1px solid var(--tk-line);
  border-radius: var(--tk-radius);
  background: var(--tk-surface);
  box-shadow: var(--tk-shadow);
}
.sandbox-shell.immersive {
  position: fixed;
  z-index: 1500;
  inset: 0;
  height: auto;
  border: 0;
  border-radius: 0;
}
.sandbox-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--tk-line);
}
.sandbox-head .el-button + .el-button {
  margin-left: 0;
}
.sandbox-title {
  display: grid;
  flex: 1;
  min-width: 140px;
}
.sandbox-title strong {
  color: var(--tk-heading);
}
.muted {
  color: var(--tk-muted);
  font-size: 12px;
}
.identity {
  display: flex;
  align-items: center;
  gap: 6px;
}
.identity-select {
  width: 116px;
}
.pane {
  display: flex;
  position: relative;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  background: var(--tk-bg);
}
.pane :deep(.composer) {
  background: var(--tk-surface);
}
.pane-empty {
  position: absolute;
  top: 38%;
  left: 0;
  right: 0;
  padding: 0 24px;
  color: var(--tk-text);
  font-size: 14px;
  text-align: center;
  pointer-events: none;
}
.pane-empty p {
  margin: 4px 0;
}
.pane-empty code {
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--tk-surface-2);
  font-family: var(--tk-font-mono, monospace);
}
@media (max-width: 820px) {
  .sandbox-shell {
    height: max(420px, calc(100dvh - 240px));
  }
  .sandbox-title .muted {
    display: none;
  }
}
</style>
