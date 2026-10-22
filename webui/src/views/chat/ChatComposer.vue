<script setup lang="ts">
import { computed, ref } from 'vue'
import { Close, Picture } from '@element-plus/icons-vue'
import type { ChatSendSegment } from '@/api'
import { useNarrow } from '@/composables/useNarrow'

// 输入区：文字、@、图片和回复。Enter 发送，Shift+Enter 换行；发送成功由父组件调用 clear()。
export interface Mention {
  id: string
  name: string
}
export interface ReplyTarget {
  messageId: string
  name: string
  preview: string
}

const props = defineProps<{
  /** 不能发送的原因；为空表示可以发送。 */
  blocked?: string | null
  sending?: boolean
  /** 可以 @ 的人；群聊额外提供“全体成员”。 */
  mentionable?: Mention[]
  allowAtAll?: boolean
  reply?: ReplyTarget | null
}>()
const emit = defineEmits<{ send: [content: ChatSendSegment[]]; cancelReply: [] }>()

const IMAGE_LIMIT = 4 * 1024 * 1024
const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']
const MAX_IMAGES = 4

// 手机上输入区默认一行，把高度留给消息。
const compact = useNarrow(640)
const text = ref('')
const mentions = ref<Mention[]>([])
const images = ref<string[]>([])
const picker = ref<HTMLInputElement | null>(null)
const input = ref<{ focus: () => void } | null>(null)

const empty = computed(() => !text.value.trim() && !mentions.value.length && !images.value.length)
const disabled = computed(() => !!props.blocked || props.sending)

function readImage(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

async function addImages(files: Iterable<File>) {
  for (const file of files) {
    if (!IMAGE_TYPES.includes(file.type)) {
      ElMessage.warning('只能发送 PNG、JPEG、GIF 或 WebP 图片')
      continue
    }
    if (file.size > IMAGE_LIMIT) {
      ElMessage.warning(`${file.name} 超过 4 MiB`)
      continue
    }
    if (images.value.length >= MAX_IMAGES) {
      ElMessage.warning(`一次最多发送 ${MAX_IMAGES} 张图片`)
      return
    }
    images.value = [...images.value, await readImage(file)]
  }
}

function onPick(event: Event) {
  const input = event.target as HTMLInputElement
  void addImages(input.files ?? [])
  input.value = ''
}
function onPaste(event: ClipboardEvent) {
  const files = [...(event.clipboardData?.files ?? [])].filter((file) =>
    file.type.startsWith('image/'),
  )
  if (!files.length) return
  event.preventDefault()
  void addImages(files)
}

function mention(target: Mention) {
  if (!mentions.value.some((item) => item.id === target.id))
    mentions.value = [...mentions.value, target]
  focus()
}
function focus() {
  input.value?.focus()
}

function submit() {
  if (disabled.value || empty.value) return
  const content: ChatSendSegment[] = []
  if (props.reply) content.push({ type: 'quote', id: props.reply.messageId })
  for (const item of mentions.value) content.push({ type: 'at', id: item.id, name: item.name })
  const body = text.value.trim()
  if (body) content.push({ type: 'text', text: mentions.value.length ? ` ${body}` : body })
  for (const src of images.value) content.push({ type: 'image', src })
  emit('send', content)
}

function onKeydown(event: Event | KeyboardEvent) {
  // 输入法组字时的 Enter 用来确认候选词。
  if (!(event instanceof KeyboardEvent) || event.key !== 'Enter') return
  if (event.shiftKey || event.isComposing) return
  event.preventDefault()
  submit()
}

function clear() {
  text.value = ''
  mentions.value = []
  images.value = []
}

defineExpose({ clear, mention, focus })
</script>

<template>
  <div class="composer" :class="{ blocked: !!blocked }">
    <div class="toolbar">
      <el-dropdown
        trigger="click"
        :disabled="!!blocked || (!mentionable?.length && !allowAtAll)"
        max-height="260px"
        @command="mention"
      >
        <el-button text size="small" :disabled="!!blocked" title="@ 成员" aria-label="@ 成员">
          <span class="at-icon">@</span>
        </el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-if="allowAtAll" :command="{ id: 'all', name: '全体成员' }">
              全体成员
            </el-dropdown-item>
            <el-dropdown-item v-for="item in mentionable" :key="item.id" :command="item">
              {{ item.name || item.id }}
              <span class="mention-id">{{ item.id }}</span>
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <el-button
        text
        size="small"
        :icon="Picture"
        :disabled="!!blocked"
        title="添加图片"
        aria-label="添加图片"
        @click="picker?.click()"
      />
      <input
        ref="picker"
        class="picker"
        type="file"
        accept="image/png,image/jpeg,image/gif,image/webp"
        multiple
        @change="onPick"
      />
      <template v-if="compact">
        <span class="spacer" />
        <el-button
          type="primary"
          size="small"
          :loading="sending"
          :disabled="disabled || empty"
          @click="submit"
        >
          发送
        </el-button>
      </template>
    </div>
    <div v-if="reply || mentions.length || images.length" class="attachments">
      <span v-if="reply" class="pill reply">
        <span class="reply-text">回复 {{ reply.name }}：{{ reply.preview || '一条消息' }}</span>
        <el-icon class="remove" aria-label="取消回复" @click="emit('cancelReply')"
          ><Close
        /></el-icon>
      </span>
      <span v-for="item in mentions" :key="item.id" class="pill">
        @{{ item.name || item.id }}
        <el-icon
          class="remove"
          :aria-label="`取消 @${item.name || item.id}`"
          @click="mentions = mentions.filter((value) => value.id !== item.id)"
          ><Close
        /></el-icon>
      </span>
      <span v-for="(src, index) in images" :key="index" class="thumb">
        <img :src="src" alt="待发送的图片" />
        <el-icon
          class="remove"
          aria-label="移除图片"
          @click="images = images.filter((_, at) => at !== index)"
          ><Close
        /></el-icon>
      </span>
    </div>
    <el-input
      ref="input"
      v-model="text"
      class="input"
      type="textarea"
      :autosize="{ minRows: compact ? 1 : 2, maxRows: compact ? 5 : 8 }"
      resize="none"
      maxlength="4000"
      :disabled="!!blocked"
      :placeholder="blocked || '输入消息，可直接粘贴图片'"
      aria-label="消息内容"
      @keydown="onKeydown"
      @paste="onPaste"
    />
    <div v-if="!compact" class="footer">
      <span class="hint">Enter 发送，Shift+Enter 换行</span>
      <el-button
        type="primary"
        :loading="sending"
        :disabled="disabled || empty"
        class="send"
        @click="submit"
      >
        发送
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.composer {
  display: grid;
  gap: 4px;
  padding: 6px 14px 12px;
  border-top: 1px solid var(--tk-line);
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-left: -6px;
}
.toolbar .el-button {
  font-size: 16px;
}
.toolbar .el-button + .el-button {
  margin-left: 0;
}
.spacer {
  flex: 1;
}
.at-icon {
  font-weight: 600;
}
.attachments {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--tk-primary-soft);
  color: var(--tk-primary);
  font-size: 12px;
}
.pill.reply {
  background: var(--tk-surface-2);
  color: var(--tk-muted);
}
.reply-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.remove {
  flex: none;
  cursor: pointer;
}
.thumb {
  position: relative;
  width: 56px;
  height: 56px;
  overflow: hidden;
  border: 1px solid var(--tk-line);
  border-radius: 6px;
}
.thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.thumb .remove {
  position: absolute;
  top: 2px;
  right: 2px;
  padding: 2px;
  border-radius: 50%;
  background: rgb(0 0 0 / 55%);
  color: #fff;
}
/* 和聊天软件一样去掉输入框边框，整块输入区就是编辑区。 */
.input :deep(.el-textarea__inner) {
  padding: 2px 0;
  background: transparent;
  box-shadow: none;
  font-size: 14px;
  line-height: 1.6;
}
.input :deep(.el-textarea__inner:focus) {
  box-shadow: none;
}
.input :deep(.el-input__count) {
  background: transparent;
}
.footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
}
.hint {
  color: var(--tk-muted);
  font-size: 12px;
}
.send {
  min-width: 76px;
}
.picker {
  display: none;
}
.mention-id {
  margin-left: 8px;
  color: var(--tk-muted);
  font-size: 12px;
}
@media (hover: none), (max-width: 640px) {
  .hint {
    display: none;
  }
}
</style>
