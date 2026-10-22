<script setup lang="ts">
import { computed, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, FullScreen, ScaleToOriginal, Search } from '@element-plus/icons-vue'
import {
  api,
  ApiError,
  type AccountSummary,
  type ChatConversation,
  type ChatConversationsData,
  type ChatMessage,
  type ChatSendSegment,
} from '@/api'
import PageContainer from '@/components/PageContainer.vue'
import QqAvatar from '@/components/QqAvatar.vue'
import { parseTime } from '@/composables/format'
import { useNarrow } from '@/composables/useNarrow'
import { usePanelData } from '@/composables/usePanelData'
import ChatComposer, { type Mention, type ReplyTarget } from './chat/ChatComposer.vue'
import ChatMessages from './chat/ChatMessages.vue'
import { previewOf, useImmersive } from './chat/shared'

// 以 bot 账号的身份聊天：左边是账号绑定的群和最近的私聊，右边是消息和输入区。
// 新消息通过实时通道的 chat 信号触发增量拉取；窄屏时列表和消息分两屏。
const { accounts, chatSignal } = usePanelData()
const route = useRoute()
const router = useRouter()
const narrow = useNarrow(820)

const errorText = (error: unknown) =>
  error instanceof ApiError ? error.message : String((error as Error)?.message ?? error)
const keyOf = (account: AccountSummary) => `${account.platform}:${account.id}`
const queryText = (value: unknown) => (typeof value === 'string' ? value : '')

// ---------------------------------------------------------------- 账号

const accountList = computed(() => accounts.data.value?.accounts ?? [])
const accountKey = ref('')
const account = computed(
  () => accountList.value.find((item) => keyOf(item) === accountKey.value) ?? null,
)
/** 地址栏带来的会话，在会话列表第一次加载后打开。 */
let pendingChat = queryText(route.query.chat)

watch(
  accountList,
  (list) => {
    if (list.some((item) => keyOf(item) === accountKey.value)) return
    const wanted = queryText(route.query.account)
    const next =
      list.find((item) => keyOf(item) === wanted) ?? list.find((item) => item.online) ?? list[0]
    accountKey.value = next ? keyOf(next) : ''
  },
  { immediate: true },
)

// ---------------------------------------------------------------- 会话列表

const data = shallowRef<ChatConversationsData | null>(null)
const listError = ref('')
const search = ref('')

async function loadConversations() {
  const current = account.value
  if (!current) return
  const target = accountKey.value
  try {
    const value = await api.manage.chatConversations(current.platform, current.id)
    if (target !== accountKey.value) return
    data.value = value
    listError.value = ''
    if (pendingChat && value.conversations.some((item) => item.channel_id === pendingChat))
      void open(pendingChat)
    pendingChat = ''
  } catch (error) {
    if (target === accountKey.value) listError.value = errorText(error)
  }
}

watch(
  accountKey,
  (next, previous) => {
    data.value = null
    listError.value = ''
    if (previous) close()
    if (next) void loadConversations()
  },
  { immediate: true },
)

const conversations = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  const rows = (data.value?.conversations ?? []).filter(
    (item) =>
      !keyword || item.name.toLowerCase().includes(keyword) || item.peer_id.includes(keyword),
  )
  const time = (item: ChatConversation) => parseTime(item.last?.created_at)?.getTime() ?? 0
  return rows.sort(
    (a, b) =>
      time(b) - time(a) || (a.name || a.peer_id).localeCompare(b.name || b.peer_id, 'zh-CN'),
  )
})

function shortTime(value: string | undefined): string {
  const date = parseTime(value)
  if (!date) return ''
  if (date.toDateString() === new Date().toDateString())
    return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  return `${date.getMonth() + 1}/${date.getDate()}`
}

function lastLine(item: ChatConversation): string {
  if (!item.last) return item.muted ? '禁言中' : '暂无消息'
  const who = item.last.outgoing
    ? '我：'
    : item.chat_type === 'group' && item.last.user_name
      ? `${item.last.user_name}：`
      : ''
  return `${who}${item.last.preview}`
}

// ---------------------------------------------------------------- 消息

const channel = ref('')
const current = computed(
  () => data.value?.conversations.find((item) => item.channel_id === channel.value) ?? null,
)
const messages = shallowRef<ChatMessage[]>([])
const hasMore = ref(false)
const loading = ref(false)
const messagesError = ref('')
const list = ref<InstanceType<typeof ChatMessages> | null>(null)
/** 每次切换会话递增，使上一个会话的在途请求作废。 */
let token = 0

const immersive = useImmersive()

function close() {
  token += 1
  channel.value = ''
  messages.value = []
  hasMore.value = false
  reply.value = null
}

async function open(id: string) {
  const current = account.value
  if (!current) return
  close()
  channel.value = id
  void router.replace({ query: { ...route.query, account: accountKey.value, chat: id } })
  const ticket = token
  loading.value = true
  messagesError.value = ''
  try {
    const result = await api.manage.chatMessages(current.platform, current.id, id)
    if (ticket !== token) return
    messages.value = result.messages
    hasMore.value = result.has_more
  } catch (error) {
    if (ticket === token) messagesError.value = errorText(error)
  } finally {
    if (ticket === token) loading.value = false
  }
}

async function loadOlder() {
  const current = account.value
  const first = messages.value[0]
  if (!current || !first || loading.value || !hasMore.value) return
  const ticket = token
  loading.value = true
  try {
    const result = await api.manage.chatMessages(current.platform, current.id, channel.value, {
      before: first.id,
    })
    if (ticket !== token) return
    messages.value = [...result.messages, ...messages.value]
    hasMore.value = result.has_more
  } catch (error) {
    if (ticket === token) ElMessage.error(`加载更早的消息失败：${errorText(error)}`)
  } finally {
    if (ticket === token) loading.value = false
  }
}

/** 拉取最后一条之后的新消息；积压超过一页时直接换成最新一页。 */
async function loadNewer() {
  const current = account.value
  if (!current || !channel.value || loading.value) return
  const ticket = token
  const last = messages.value.at(-1)
  try {
    const result = await api.manage.chatMessages(
      current.platform,
      current.id,
      channel.value,
      last ? { after: last.id } : {},
    )
    if (ticket !== token) return
    if (!last || result.has_more) {
      if (last && result.has_more) {
        const latest = await api.manage.chatMessages(current.platform, current.id, channel.value)
        if (ticket !== token) return
        messages.value = latest.messages
        hasMore.value = latest.has_more
      } else {
        messages.value = result.messages
        hasMore.value = result.has_more
      }
      return
    }
    const known = messages.value.at(-1)?.id ?? 0
    const fresh = result.messages.filter((item) => item.id > known)
    if (fresh.length) messages.value = [...messages.value, ...fresh]
  } catch {
    // 实时增量失败不打扰用户，下一次信号或手动切换会话时会再拉。
  }
}

watch(chatSignal, () => {
  void loadConversations()
  void loadNewer()
})

// ---------------------------------------------------------------- 发送

const composer = ref<InstanceType<typeof ChatComposer> | null>(null)
const sending = ref(false)
const reply = ref<ReplyTarget | null>(null)

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

const mentionable = computed<Mention[]>(() => {
  if (current.value?.chat_type !== 'group') return []
  const seen = new Map<string, Mention>()
  for (const message of [...messages.value].reverse()) {
    if (message.outgoing || seen.has(message.user_id)) continue
    seen.set(message.user_id, { id: message.user_id, name: message.user_name })
    if (seen.size >= 30) break
  }
  return [...seen.values()]
})

const blocked = computed(() => {
  if (!account.value) return '请先选择账号'
  if (data.value && !data.value.online) return '账号离线，暂时不能发送'
  if (current.value?.muted) return '账号在这个群被禁言，暂时不能发言'
  return null
})

async function send(content: ChatSendSegment[]) {
  const current = account.value
  if (!current || !channel.value) return
  sending.value = true
  try {
    await api.manage.chatSend({
      platform: current.platform,
      account: current.id,
      channel: channel.value,
      content,
    })
    composer.value?.clear()
    reply.value = null
    await loadNewer()
    list.value?.scrollToBottom()
  } catch (error) {
    const text = errorText(error)
    ElMessage.error(text.startsWith('发送失败') ? text : `发送失败：${text}`)
  } finally {
    sending.value = false
  }
}
</script>

<template>
  <PageContainer>
    <el-empty
      v-if="accounts.data.value && !accountList.length"
      :image-size="64"
      description="还没有接入的 bot 账号，协议端连上后会出现在这里"
    />
    <div v-else class="chat-shell" :class="{ narrow, immersive, open: !!channel }">
      <aside class="side">
        <div class="side-head">
          <el-select
            v-if="accountList.length > 1"
            v-model="accountKey"
            aria-label="选择账号"
            class="account-select"
          >
            <el-option
              v-for="item in accountList"
              :key="keyOf(item)"
              :value="keyOf(item)"
              :label="item.name || item.id"
            >
              <span class="account-option">
                <QqAvatar
                  :id="item.id"
                  :name="item.name"
                  :platform="item.platform"
                  :size="20"
                  kind="user"
                />
                {{ item.name || item.id }}
                <span class="muted">{{ item.online ? item.id : '离线' }}</span>
              </span>
            </el-option>
          </el-select>
          <div v-else-if="account" class="account-single">
            <QqAvatar
              :id="account.id"
              :name="account.name"
              :platform="account.platform"
              :size="24"
              kind="user"
            />
            <strong>{{ account.name || account.id }}</strong>
            <span class="muted">{{ account.online ? account.id : '离线' }}</span>
          </div>
          <el-input
            v-model="search"
            :prefix-icon="Search"
            clearable
            placeholder="搜索群名、群号或 QQ 号"
            aria-label="搜索会话"
          />
        </div>
        <div class="conversations">
          <el-alert v-if="listError && !data" type="error" :closable="false" show-icon>
            <template #title>读取会话失败：{{ listError }}</template>
            <el-button size="small" @click="loadConversations">重试</el-button>
          </el-alert>
          <el-skeleton v-else-if="!data" :rows="5" animated class="skeleton" />
          <el-empty
            v-else-if="!conversations.length"
            :image-size="48"
            :description="search ? '没有匹配的会话' : '还没有群聊或私聊'"
          />
          <button
            v-for="item in conversations"
            v-else
            :key="item.channel_id"
            type="button"
            :class="['conversation', { active: item.channel_id === channel }]"
            @click="open(item.channel_id)"
          >
            <QqAvatar
              :id="item.peer_id"
              :name="item.name"
              :platform="account?.platform"
              :kind="item.chat_type === 'group' ? 'group' : 'user'"
              :size="38"
            />
            <span class="conversation-body">
              <span class="conversation-top">
                <span class="conversation-name">{{ item.name || item.peer_id }}</span>
                <span class="conversation-time">{{ shortTime(item.last?.created_at) }}</span>
              </span>
              <span class="conversation-line">
                <span v-if="item.chat_type === 'private'" class="tag">私聊</span>
                <span v-if="item.muted" class="tag warn">禁言</span>
                {{ lastLine(item) }}
              </span>
            </span>
          </button>
        </div>
        <el-button
          v-if="immersive && narrow && !channel"
          class="side-exit"
          text
          :icon="ScaleToOriginal"
          @click="immersive = false"
        >
          退出全屏
        </el-button>
        <p v-if="data" class="side-foot">
          {{
            data.enabled
              ? `消息记录保留 ${data.retention_days} 天`
              : '消息记录已关闭，只能发送，看不到历史消息'
          }}
        </p>
      </aside>

      <section class="pane">
        <template v-if="channel">
          <header class="pane-head">
            <el-button
              v-if="narrow"
              text
              :icon="ArrowLeft"
              aria-label="返回会话列表"
              @click="close"
            />
            <QqAvatar
              :id="current?.peer_id ?? channel"
              :name="current?.name"
              :platform="account?.platform"
              :kind="current?.chat_type === 'private' ? 'user' : 'group'"
              :size="30"
            />
            <div class="pane-title">
              <strong>{{ current?.name || current?.peer_id || channel }}</strong>
              <span class="muted tk-mono">{{ current?.peer_id }}</span>
            </div>
            <span v-if="current?.muted" class="tag warn">禁言中</span>
            <el-button
              text
              :icon="immersive ? ScaleToOriginal : FullScreen"
              :title="immersive ? '退出全屏（Esc）' : '全屏'"
              :aria-label="immersive ? '退出全屏' : '全屏'"
              @click="immersive = !immersive"
            />
          </header>
          <el-alert
            v-if="messagesError"
            type="error"
            :closable="false"
            show-icon
            :title="`读取消息失败：${messagesError}`"
          />
          <ChatMessages
            ref="list"
            :messages="messages"
            :has-more="hasMore"
            :loading="loading"
            :platform="account?.platform ?? 'onebot'"
            :chat-type="current?.chat_type"
            @load-more="loadOlder"
            @reply="replyTo"
            @mention="mentionOf"
          />
          <p v-if="!loading && !messages.length && !messagesError" class="pane-empty">
            {{ data?.enabled ? '这里还没有消息记录' : '消息记录已关闭' }}
          </p>
          <ChatComposer
            ref="composer"
            :blocked="blocked"
            :sending="sending"
            :mentionable="mentionable"
            :allow-at-all="current?.chat_type === 'group'"
            :reply="reply"
            @send="send"
            @cancel-reply="reply = null"
          />
        </template>
        <el-empty
          v-else
          :image-size="64"
          description="选择左侧的群聊或私聊"
          class="pane-placeholder"
        />
      </section>
    </div>
  </PageContainer>
</template>

<style scoped>
.chat-shell {
  display: grid;
  grid-template-columns: minmax(240px, 300px) minmax(0, 1fr);
  height: max(460px, calc(100dvh - 150px));
  overflow: hidden;
  border: 1px solid var(--tk-line);
  border-radius: var(--tk-radius);
  background: var(--tk-surface);
  box-shadow: var(--tk-shadow);
}
.side {
  display: flex;
  flex-direction: column;
  min-height: 0;
  border-right: 1px solid var(--tk-line);
}
.side-head {
  display: grid;
  gap: 8px;
  padding: 12px;
  border-bottom: 1px solid var(--tk-line);
}
.account-single,
.account-option {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.account-single strong {
  overflow: hidden;
  color: var(--tk-heading);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.muted {
  color: var(--tk-muted);
  font-size: 12px;
}
.conversations {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 6px;
}
.skeleton {
  padding: 8px;
}
.conversation {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px;
  border: 0;
  border-radius: 8px;
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.conversation:hover {
  background: var(--tk-surface-2);
}
.conversation.active {
  background: var(--tk-primary-soft);
}
.conversation:focus-visible {
  outline: 2px solid var(--tk-primary);
  outline-offset: -2px;
}
.conversation-body {
  display: grid;
  flex: 1;
  gap: 2px;
  min-width: 0;
}
.conversation-top {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.conversation-name {
  flex: 1;
  overflow: hidden;
  color: var(--tk-heading);
  font-size: 14px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conversation-time {
  flex: none;
  color: var(--tk-muted);
  font-size: 11px;
}
.conversation-line {
  overflow: hidden;
  color: var(--tk-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tag {
  display: inline-block;
  margin-right: 4px;
  padding: 0 5px;
  border: 1px solid var(--tk-line);
  border-radius: 4px;
  color: var(--tk-muted);
  font-size: 11px;
  line-height: 16px;
}
.tag.warn {
  border-color: var(--el-color-warning);
  color: var(--el-color-warning);
}
.side-foot {
  margin: 0;
  padding: 8px 12px;
  border-top: 1px solid var(--tk-line);
  color: var(--tk-muted);
  font-size: 12px;
}
.pane {
  display: flex;
  position: relative;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--tk-bg);
}
.pane-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--tk-line);
  background: var(--tk-surface);
}
.pane-title {
  display: grid;
  flex: 1;
  min-width: 0;
}
.pane-title strong {
  overflow: hidden;
  color: var(--tk-heading);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pane-empty {
  position: absolute;
  top: 45%;
  left: 0;
  right: 0;
  margin: 0;
  color: var(--tk-muted);
  font-size: 13px;
  text-align: center;
  pointer-events: none;
}
.pane-placeholder {
  margin: auto;
}
.pane :deep(.composer) {
  background: var(--tk-surface);
}
.chat-shell.immersive {
  position: fixed;
  z-index: 1500;
  inset: 0;
  height: auto;
  border: 0;
  border-radius: 0;
}
.side-exit {
  margin: 0 12px 4px;
}
@media (max-width: 820px) {
  .chat-shell {
    grid-template-columns: minmax(0, 1fr);
    height: max(420px, calc(100dvh - 240px));
  }
  .chat-shell.narrow.open .side,
  .chat-shell.narrow:not(.open) .pane {
    display: none;
  }
  .side {
    border-right: 0;
  }
}
</style>
