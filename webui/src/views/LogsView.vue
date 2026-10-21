<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { api, ApiError, type LogData, type LogLevel } from '@/api'
import PageContainer from '@/components/PageContainer.vue'
import PagePanel from '@/components/PagePanel.vue'
import { parseTime } from '@/composables/format'

const LIVE_INTERVAL_MS = 3000
const LIVE_KEEP = 1000
const LEVELS: LogLevel[] = ['INFO', 'SUCCESS', 'WARNING', 'ERROR', 'CRITICAL']

interface Row {
  key: number
  text: string
  level: string
}

const LIVE = 'live' // 来源下拉里“实时日志”的取值；历史文件名都以 .log 系列后缀结尾，不会冲突
const ALL = 'ALL'
const source = ref(LIVE)
const input = ref('')
const query = ref('') // 已提交的搜索词；轮询和切换来源都用它
const levelChoice = ref<LogLevel | typeof ALL>(ALL)

const rows = ref<Row[]>([])
const files = ref<string[]>([])
const historyEnabled = ref(true)
const truncated = ref(false)
const matched = ref(0)
const scanLimit = ref(0)
const loaded = ref(false)
const loading = ref(false)
const error = ref('')
const output = ref<HTMLElement | null>(null)

let cursor = 0
let serial = 0
let requestId = 0 // 来源 / 搜索变化后，使在途请求作废
let busy = false
let timer: ReturnType<typeof setInterval> | undefined

const live = computed(() => source.value === LIVE)
const file = computed(() => (live.value ? '' : source.value))
const level = computed(() => (levelChoice.value === ALL ? '' : levelChoice.value))

function pad(value: number) {
  return String(value).padStart(2, '0')
}
function stamp(iso: string): string {
  const date = parseTime(iso)
  if (!date) return iso
  const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
  return `${day} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}
const HISTORY_LEVEL = /\b(SUCCESS|WARNING|ERROR|CRITICAL)\b/

function applyLive(data: Extract<LogData, { records: unknown }>, reset: boolean) {
  if (reset || data.reset) rows.value = []
  const added = data.records.map((record) => ({
    key: ++serial,
    level: record.level,
    text: `${stamp(record.time)}  ${record.level.padEnd(8)} ${record.message}`,
  }))
  rows.value = [...rows.value, ...added].slice(-LIVE_KEEP)
  cursor = data.cursor
  truncated.value = data.truncated
}

function applyHistory(data: Extract<LogData, { lines: unknown }>) {
  rows.value = data.lines.map((text) => ({
    key: ++serial,
    level: HISTORY_LEVEL.exec(text)?.[1] ?? '',
    text,
  }))
  truncated.value = data.truncated
  matched.value = data.matched
  scanLimit.value = data.scan_limit_bytes
}

/** reset 表示来源或搜索条件变了：清空旧内容后重新读取；否则是实时模式的增量轮询。 */
async function load(reset = false) {
  if (reset) {
    requestId += 1
    cursor = 0
    rows.value = []
    truncated.value = false
    loaded.value = false
    loading.value = true
    error.value = ''
  } else if (busy) {
    return
  }
  const current = requestId
  busy = true
  try {
    const data = await api.manage.logs(
      live.value
        ? { q: query.value, after: reset ? 0 : cursor, level: level.value }
        : { q: query.value, file: file.value },
    )
    if (current !== requestId) return
    const atBottom = isAtBottom()
    files.value = data.files
    historyEnabled.value = data.history_enabled
    if ('lines' in data) applyHistory(data)
    else applyLive(data, reset)
    if (error.value) error.value = ''
    loaded.value = true
    await nextTick()
    if (reset || atBottom) scrollToBottom()
  } catch (reason) {
    if (current !== requestId) return
    const message = reason instanceof ApiError ? reason.message : String(reason)
    // 轮询持续失败只在首次失败时提示，避免每 3 秒弹一次。
    if (!error.value) ElMessage.error(`读取日志失败：${message}`)
    error.value = message
  } finally {
    if (current === requestId) loading.value = false
    busy = false
  }
}

function isAtBottom() {
  const el = output.value
  return !el || el.scrollTop + el.clientHeight >= el.scrollHeight - 40
}
function scrollToBottom() {
  if (output.value) output.value.scrollTop = output.value.scrollHeight
}

function search() {
  query.value = input.value.trim()
  void load(true)
}
function changeSource() {
  // 历史文件不支持级别筛选，切换时一并清掉。
  if (!live.value) levelChoice.value = ALL
  void load(true)
}

function tick() {
  if (live.value && !document.hidden) void load()
}
function onVisible() {
  tick()
}

onMounted(() => {
  void load(true)
  timer = setInterval(tick, LIVE_INTERVAL_MS)
  document.addEventListener('visibilitychange', onVisible)
})
onBeforeUnmount(() => {
  clearInterval(timer)
  document.removeEventListener('visibilitychange', onVisible)
  requestId += 1
})

async function copy() {
  const text = rows.value.map((row) => row.text).join('\n')
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
    } else {
      // 面板常以 http 访问，此时没有 Clipboard API，退回到选中文本复制。
      const area = document.createElement('textarea')
      area.value = text
      area.style.position = 'fixed'
      area.style.opacity = '0'
      document.body.append(area)
      area.select()
      const ok = document.execCommand('copy')
      area.remove()
      if (!ok) throw new Error('copy failed')
    }
    ElMessage.success(`已复制 ${rows.value.length} 行日志`)
  } catch {
    ElMessage.error('复制失败，请手动选中日志内容')
  }
}

const scanText = computed(() => `${Math.round(scanLimit.value / 1024 / 1024)} MiB`)
const status = computed(() => {
  if (live.value) {
    return `每 3 秒刷新 · 最近 ${rows.value.length} 条（最多保留 ${LIVE_KEEP} 条）`
  }
  return `${file.value} · ${matched.value} 条匹配，显示最后 ${rows.value.length} 条`
})
const emptyText = computed(() => {
  if (error.value && !loaded.value) return `日志读取失败：${error.value}`
  if (query.value) return '没有匹配的日志'
  if (live.value) return '暂无日志'
  return '文件中没有内容'
})
</script>

<template>
  <PageContainer>
    <PagePanel>
      <form class="toolbar" @submit.prevent="search">
        <el-select
          v-model="source"
          class="source"
          aria-label="日志来源"
          :loading="loading"
          @change="changeSource"
        >
          <el-option label="实时日志" :value="LIVE" />
          <el-option v-for="name in files" :key="name" :label="name" :value="name" />
        </el-select>
        <el-select
          v-if="live"
          v-model="levelChoice"
          class="level"
          aria-label="日志级别"
          @change="search"
        >
          <el-option label="全部级别" :value="ALL" />
          <el-option v-for="item in LEVELS" :key="item" :label="item" :value="item" />
        </el-select>
        <el-input
          v-model="input"
          class="search"
          maxlength="200"
          clearable
          placeholder="搜索关键字"
          aria-label="搜索日志"
          @clear="search"
        />
        <el-button type="primary" native-type="submit" :loading="loading">查询</el-button>
        <el-button :disabled="!rows.length" @click="copy">复制</el-button>
      </form>

      <p class="status" role="status">
        <template v-if="loaded">
          {{ status }}
          <el-tag v-if="!live && truncated" type="warning" size="small" effect="light">
            结果已截断
          </el-tag>
        </template>
        <template v-else>&nbsp;</template>
      </p>
      <p v-if="!live && loaded" class="hint">
        历史检索只扫描每个文件末尾 {{ scanText }}，且最多显示最后 500
        条匹配；超出时请缩小搜索范围或选择更早的轮转文件。
      </p>
      <p v-else-if="live && loaded && !historyEnabled" class="hint">
        宿主未开启日志落盘，只有本次运行的实时日志可查看。
      </p>

      <div
        ref="output"
        class="log-output"
        tabindex="0"
        role="log"
        aria-label="日志内容"
        v-loading="loading && !loaded"
      >
        <div v-for="row in rows" :key="row.key" class="row" :class="row.level && `lv-${row.level}`">
          {{ row.text }}
        </div>
        <div v-if="!rows.length && !loading" class="empty">
          <span>{{ emptyText }}</span>
          <el-button v-if="error && !loaded" size="small" @click="load(true)">重试</el-button>
        </div>
      </div>
    </PagePanel>
  </PageContainer>
</template>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.toolbar .el-button {
  margin: 0;
}
.source {
  width: 220px;
}
.level {
  width: 130px;
}
.search {
  flex: 1 1 200px;
  min-width: 0;
}
.status {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 24px;
  margin: 12px 0 4px;
  color: var(--tk-muted);
  font-size: 12px;
}
.hint {
  margin: 0 0 8px;
  color: var(--tk-muted);
  font-size: 12px;
}
.log-output {
  height: clamp(300px, calc(100dvh - 340px), 720px);
  overflow: auto;
  margin-top: 8px;
  padding: 12px 14px;
  border-radius: 10px;
  background: #0e1326;
  color: #cfd6ec;
  font-family: var(--tk-font-mono);
  font-size: 12.5px;
  line-height: 1.65;
}
.row {
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.row.lv-SUCCESS {
  color: #6fd39a;
}
.row.lv-WARNING {
  color: #f0c56a;
}
.row.lv-ERROR,
.row.lv-CRITICAL {
  color: #ff8c84;
}
.empty {
  display: flex;
  height: 100%;
  min-height: 120px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: #8b95b6;
  font-family: var(--tk-font);
  font-size: 13px;
  text-align: center;
}
@media (max-width: 640px) {
  .source,
  .level {
    width: 100%;
    flex: 1 1 100%;
  }
  .log-output {
    height: clamp(280px, calc(100dvh - 400px), 600px);
  }
}
</style>
