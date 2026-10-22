<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ChatLineRound } from '@element-plus/icons-vue'
import type { ChatMessage, ChatSegment } from '@/api'
import QqAvatar from '@/components/QqAvatar.vue'
import { parseTime } from '@/composables/format'
import ChatContent from './ChatContent.vue'

// 消息流：往上滚到顶部时加载更早的消息并保持位置；贴着底部时新消息自动跟随。
// 右键（或悬停按钮）打开消息菜单：回复、@ 对方、复制文字；点引用跳到原消息。
const props = defineProps<{
  messages: ChatMessage[]
  hasMore: boolean
  loading: boolean
  platform: string
  /** 私聊只有两个人，不重复显示名字。 */
  chatType?: 'group' | 'private'
}>()
const emit = defineEmits<{
  loadMore: []
  reply: [message: ChatMessage]
  mention: [message: ChatMessage]
}>()

/** 相邻消息间隔超过这个时长就插一条时间分隔。 */
const GAP_MS = 5 * 60_000
/** 同一个人连续发言、间隔在这之内时不重复显示头像和名字。 */
const MERGE_MS = 3 * 60_000

const box = ref<HTMLElement | null>(null)
const flashed = ref<number | null>(null)
let stick = true
let lastTop = 0

function pad(value: number) {
  return String(value).padStart(2, '0')
}
function clock(date: Date) {
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`
}
function divider(date: Date): string {
  const now = new Date()
  if (date.toDateString() === now.toDateString()) return clock(date)
  const yesterday = new Date(now.getTime() - 86_400_000)
  if (date.toDateString() === yesterday.toDateString()) return `昨天 ${clock(date)}`
  return `${date.getMonth() + 1}月${date.getDate()}日 ${clock(date)}`
}

/** 只有图片的消息不套气泡，和 QQ 一样直接显示图片。 */
function bare(content: ChatSegment[]) {
  return content.length > 0 && content.every((item) => item.type === 'image' && !!item.src)
}

const rows = computed(() =>
  props.messages.map((message, index) => {
    const date = parseTime(message.created_at) ?? new Date(0)
    const at = date.getTime()
    const previous = props.messages[index - 1]
    const previousAt = previous ? (parseTime(previous.created_at)?.getTime() ?? 0) : 0
    const gap = !previous || at - previousAt > GAP_MS
    return {
      message,
      divider: gap ? divider(date) : '',
      merged:
        !gap &&
        previous?.user_id === message.user_id &&
        previous.outgoing === message.outgoing &&
        at - previousAt <= MERGE_MS,
      title: `${date.toLocaleDateString()} ${clock(date)}`,
      bare: bare(message.content),
    }
  }),
)

function nearBottom(element: HTMLElement) {
  return element.scrollHeight - element.scrollTop - element.clientHeight < 80
}
function onScroll() {
  const element = box.value
  if (!element) return
  // 只有往上滚才取消贴底：程序滚到底之后，图片加载撑高内容，随后到达的 scroll
  // 事件会看到“离底部很远”，按距离判断会误以为用户翻走了。
  const top = element.scrollTop
  if (nearBottom(element)) stick = true
  else if (top < lastTop) stick = false
  lastTop = top
  if (element.scrollTop < 60 && props.hasMore && !props.loading) emit('loadMore')
}
function scrollToBottom() {
  const element = box.value
  if (!element) return
  element.scrollTop = element.scrollHeight
  stick = true
}
// el-image 的 load 事件不一定冒泡到这里，所以直接观察尺寸变化。
const track = ref<HTMLElement | null>(null)
let observer: ResizeObserver | undefined
onMounted(() => {
  observer = new ResizeObserver(() => {
    if (stick) scrollToBottom()
  })
  // 内容变高（图片加载）或可视区变矮（退出全屏、输入框长高）都要重新贴底。
  if (track.value) observer.observe(track.value)
  if (box.value) observer.observe(box.value)
})
onBeforeUnmount(() => observer?.disconnect())

let flashTimer: ReturnType<typeof setTimeout> | undefined
function jump(messageId: string) {
  const target = props.messages.find((item) => item.message_id === messageId)
  const element = target && box.value?.querySelector(`[data-row="${target.id}"]`)
  if (!target || !element) {
    ElMessage.info('原消息不在已加载的记录里，往上翻翻看')
    return
  }
  element.scrollIntoView({ block: 'center', behavior: 'smooth' })
  flashed.value = target.id
  clearTimeout(flashTimer)
  flashTimer = setTimeout(() => (flashed.value = null), 1600)
}

function plainText(message: ChatMessage) {
  return message.content
    .map((item) => {
      if (item.type === 'text') return item.text
      if (item.type === 'at') return `@${item.name || item.id}`
      return ''
    })
    .join('')
    .trim()
}
async function onCommand(command: 'reply' | 'mention' | 'copy', message: ChatMessage) {
  if (command === 'reply') emit('reply', message)
  else if (command === 'mention') emit('mention', message)
  else {
    try {
      await navigator.clipboard.writeText(plainText(message))
      ElMessage.success('已复制')
    } catch {
      ElMessage.warning('浏览器不允许写入剪贴板')
    }
  }
}

watch(
  () => props.messages,
  async (next, previous) => {
    const element = box.value
    if (!element) return
    const prepended = !!previous?.length && !!next.length && next[0]!.id < previous[0]!.id
    const height = element.scrollHeight
    const top = element.scrollTop
    await nextTick()
    if (prepended) element.scrollTop = element.scrollHeight - height + top
    else if (stick || !previous?.length) scrollToBottom()
  },
  { flush: 'pre' },
)

defineExpose({ scrollToBottom })
</script>

<template>
  <div ref="box" class="chat-messages" @scroll.passive="onScroll">
    <div ref="track">
      <div v-if="hasMore || loading" class="more">
        <el-button text size="small" :loading="loading" @click="emit('loadMore')">
          加载更早的消息
        </el-button>
      </div>
      <template v-for="row in rows" :key="row.message.id">
        <div v-if="row.divider" class="divider">
          <span>{{ row.divider }}</span>
        </div>
        <div
          :data-row="row.message.id"
          :class="[
            'message',
            {
              outgoing: row.message.outgoing,
              merged: row.merged,
              flash: flashed === row.message.id,
            },
          ]"
        >
          <QqAvatar
            v-if="!row.merged"
            :id="row.message.user_id"
            :name="row.message.user_name"
            :platform="platform"
            :size="36"
            kind="user"
          />
          <span v-else class="avatar-gap" />
          <div class="body">
            <div v-if="!row.merged && chatType !== 'private'" class="name">
              {{ row.message.user_name || row.message.user_id }}
            </div>
            <div class="bubble-row">
              <el-dropdown
                trigger="contextmenu"
                placement="bottom-start"
                @command="
                  (command: 'reply' | 'mention' | 'copy') => onCommand(command, row.message)
                "
              >
                <div :class="['bubble', { bare: row.bare }]" :title="row.title">
                  <ChatContent :content="row.message.content" @jump="jump" />
                </div>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="reply" :disabled="!row.message.message_id">
                      回复
                    </el-dropdown-item>
                    <el-dropdown-item
                      v-if="chatType === 'group' && !row.message.outgoing"
                      command="mention"
                    >
                      @{{ row.message.user_name || row.message.user_id }}
                    </el-dropdown-item>
                    <el-dropdown-item command="copy" :disabled="!plainText(row.message)">
                      复制文字
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
              <el-button
                v-if="row.message.message_id"
                class="reply"
                text
                circle
                size="small"
                :icon="ChatLineRound"
                title="回复"
                aria-label="回复"
                @click="emit('reply', row.message)"
              />
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.chat-messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 8px 20px 18px;
  overscroll-behavior: contain;
}
.more {
  display: flex;
  justify-content: center;
  margin: 6px 0;
}
.divider {
  display: flex;
  justify-content: center;
  margin: 16px 0 4px;
}
.divider span {
  padding: 2px 10px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--tk-text) 8%, transparent);
  color: var(--tk-muted);
  font-size: 11px;
}
.message {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-top: 14px;
  border-radius: 10px;
  transition: background-color 300ms ease;
}
.message.merged {
  margin-top: 6px;
}
.message.flash {
  background: color-mix(in srgb, var(--tk-primary) 14%, transparent);
}
.message.outgoing {
  flex-direction: row-reverse;
}
.avatar-gap {
  flex: none;
  width: 36px;
}
.body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  max-width: min(70%, 560px);
}
.outgoing .body {
  align-items: flex-end;
}
.name {
  overflow: hidden;
  max-width: 240px;
  margin-bottom: 4px;
  color: var(--tk-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bubble-row {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.bubble-row > .el-dropdown {
  min-width: 0;
}
.outgoing .bubble-row {
  flex-direction: row-reverse;
}
.bubble {
  min-width: 0;
  padding: 8px 12px;
  border-radius: 10px;
  background: var(--tk-surface);
  box-shadow: 0 1px 2px rgb(15 23 42 / 6%);
  color: var(--tk-text);
  font-size: 14px;
}
.outgoing .bubble {
  background: var(--tk-primary);
  box-shadow: none;
  color: var(--tk-on-primary);
}
/* 主色气泡上的 @、占位和引用跟随气泡文字色，深浅主题下都有足够对比度。 */
.outgoing .bubble :deep(.at) {
  color: inherit;
  font-weight: 600;
}
.outgoing .bubble :deep(.chip),
.outgoing .bubble :deep(.quote) {
  color: color-mix(in srgb, var(--tk-on-primary) 80%, transparent);
}
.outgoing .bubble :deep(.quote) {
  border-left-color: color-mix(in srgb, var(--tk-on-primary) 55%, transparent);
  background: color-mix(in srgb, var(--tk-on-primary) 14%, transparent);
}
.bubble.bare,
.outgoing .bubble.bare {
  padding: 0;
  background: transparent;
  box-shadow: none;
}
.reply {
  flex: none;
  opacity: 0;
  transition: opacity 120ms ease;
}
.message:hover .reply,
.reply:focus-visible {
  opacity: 1;
}
@media (hover: none) {
  .reply {
    opacity: 0.55;
  }
}
@media (max-width: 640px) {
  .chat-messages {
    padding-inline: 10px;
  }
  .body {
    max-width: 78%;
  }
}
</style>
