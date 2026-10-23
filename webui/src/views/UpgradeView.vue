<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, shallowRef, watch } from 'vue'
import {
  api,
  ApiError,
  type UpgradeAction,
  type UpgradeHistoryEntry,
  type UpgradeJob,
  type UpgradeStatus,
} from '@/api'
import PageContainer from '@/components/PageContainer.vue'
import PagePanel from '@/components/PagePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import { confirmAction } from '@/composables/confirm'
import { relativeText, timeText } from '@/composables/format'
import { renderMarkdown } from '@/composables/markdown'
import { usePanelData } from '@/composables/usePanelData'
import type { StatusKind } from '@/composables/status'

const POLL_MS = 2000
// 重启超过这个时间仍连不上，提示去看宿主进程。
const SLOW_RESTART_MS = 180_000

const CHANNELS: Record<string, string> = { stable: '正式版', prerelease: '预发布（含正式版）' }
const POLICIES: Record<string, string> = {
  check: '只检查',
  download: '自动下载',
  install: '自动安装',
}

const { refresh } = usePanelData()
const status = shallowRef<UpgradeStatus | null>(null)
const loadError = ref<string | null>(null)
const checking = ref(false)
const starting = ref(false)
/** 等待重启期间连不上面板的起始时间。 */
const offlineSince = ref<number | null>(null)
const now = ref(Date.now())
const outcome = ref<{ type: 'success' | 'warning'; text: string } | null>(null)

let alive = true
let following = false
onScopeDispose(() => {
  alive = false
})

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
const errorText = (error: unknown) => (error instanceof ApiError ? error.message : String(error))
const isActive = (job: UpgradeJob | null | undefined) =>
  job?.state === 'running' || job?.state === 'restarting'

const job = computed(() => status.value?.job ?? null)
const busy = computed(() => starting.value || isActive(job.value) || offlineSince.value !== null)
const candidate = computed(() => status.value?.last_check?.candidate ?? null)
const changelog = computed(() => status.value?.last_check?.changelog ?? [])
const incompatible = computed(() => Object.entries(job.value?.incompatible ?? {}))
const comboConflict = computed(() => !!job.value?.incompatible['*'])
/** 每个不兼容插件的处理：suspend（停用并保留记录）或新版本上要装的版本；默认停用。 */
const choices = ref<Record<string, string>>({})
watch(
  () => incompatible.value.map(([plugin]) => plugin).join(),
  () => {
    choices.value = Object.fromEntries(
      incompatible.value
        .filter(([plugin]) => plugin !== '*')
        .map(([plugin]) => [plugin, 'suspend']),
    )
  },
  { immediate: true },
)
/** 可选版本从旧到新：最旧的能用版本功能变化最小，排在最前。 */
const versionsOf = (plugin: string) => [...(job.value?.options?.[plugin] ?? [])].reverse()
const slow = computed(
  () => offlineSince.value !== null && now.value - offlineSince.value > SLOW_RESTART_MS,
)

async function load() {
  try {
    status.value = await api.manage.upgrade()
    loadError.value = null
  } catch (error) {
    loadError.value = errorText(error)
  }
  if (isActive(job.value)) void follow()
}
onMounted(load)

/**
 * 升级任务进行中时轮询状态。进入重启后旧进程一直报告 restarting，随后连不上；
 * 新进程没有这个任务，据此判断重启已完成。
 */
async function follow() {
  if (following) return
  following = true
  const before = status.value?.current_version
  let restarting = false
  try {
    while (alive) {
      await sleep(POLL_MS)
      now.value = Date.now()
      let next: UpgradeStatus
      try {
        next = await api.manage.upgrade()
      } catch {
        if (restarting) offlineSince.value ??= Date.now()
        continue
      }
      offlineSince.value = null
      status.value = next
      if (next.job?.state === 'restarting') restarting = true
      if (!isActive(next.job)) break
    }
  } finally {
    following = false
    offlineSince.value = null
  }
  const current = status.value
  if (!alive || !restarting || !current || current.job) return
  void refresh() // 侧栏的版本号等共享数据也来自旧进程
  outcome.value =
    current.current_version !== before
      ? { type: 'success', text: `已重启，当前版本 ${current.current_version}` }
      : {
          type: 'warning',
          text: `已重启，但版本仍是 ${current.current_version}；新版本可能未通过启动检查并已自动回滚，请查看下方升级记录。`,
        }
}

async function check() {
  if (!status.value) return
  checking.value = true
  try {
    const result = await api.manage.checkUpgrade()
    status.value = { ...status.value, last_check: result }
    if (!result.candidate) ElMessage.success('当前已是最新版本')
  } catch (error) {
    ElMessage.error(`检查失败：${errorText(error)}`)
  } finally {
    checking.value = false
  }
}

async function start(action: UpgradeAction, withChoices = false) {
  if (!status.value || busy.value) return
  const picked = Object.values(choices.value)
  const suspended = picked.filter((choice) => choice === 'suspend').length
  const upgraded = picked.length - suspended
  const text =
    action === 'rollback'
      ? [
          `将重启并回到上一个版本 ${status.value.previous_version}。`,
          '回滚后插件环境也会恢复到当时的状态。',
        ]
      : [
          `将下载并校验 ${candidate.value?.tag ?? '新版本'}，通过后自动重启进入新版本；新版本启动失败会自动回滚。`,
          withChoices && suspended
            ? `${suspended} 个插件会先停用，记录、数据和配置都保留，之后可在待处理里恢复。`
            : '',
          withChoices && upgraded
            ? `${upgraded} 个插件会在进入新版本后装上所选版本，装不上的保持停用。`
            : '',
        ]
  const ok = await confirmAction(
    text.filter(Boolean).join(''),
    action === 'rollback' ? '回滚宿主' : '升级宿主',
    action === 'rollback' ? '回滚' : '升级',
  )
  if (!ok) return
  starting.value = true
  outcome.value = null
  try {
    const started = await api.manage.startUpgrade({
      action,
      confirm: true,
      ...(withChoices ? { plugins: { ...choices.value } } : {}),
    })
    status.value = { ...status.value, job: started }
    void follow()
  } catch (error) {
    ElMessage.error(`${action === 'rollback' ? '回滚' : '升级'}失败：${errorText(error)}`)
  } finally {
    starting.value = false
  }
}

const jobTitle = computed(() => {
  const current = job.value
  if (!current) return ''
  const verb = current.action === 'rollback' ? '回滚' : '升级'
  if (offlineSince.value !== null || current.state === 'restarting') return '正在重启'
  if (current.state === 'running') return current.stage || `正在${verb}`
  return current.state === 'failed' ? `${verb}失败` : `${verb}完成`
})
const jobType = computed(() =>
  job.value?.state === 'failed' ? 'error' : job.value?.state === 'succeeded' ? 'success' : 'info',
)

const HISTORY: Record<string, [string, StatusKind]> = {
  'install:success': ['升级成功', 'on'],
  'install:failed': ['升级失败', 'danger'],
  'rollback:success': ['回滚成功', 'on'],
  'rollback:failed': ['回滚失败', 'danger'],
  'startup:failed': ['新版本启动失败', 'danger'],
  'download:failed': ['下载失败', 'danger'],
  'restart:requested': ['重启', 'off'],
}
function historyTag(entry: UpgradeHistoryEntry): [string, StatusKind] {
  const [label, kind] = HISTORY[`${entry.action}:${entry.result}`] ?? [entry.result, 'off']
  return [entry.automatic && entry.action === 'rollback' ? `自动${label}` : label, kind]
}
const versions = (entry: UpgradeHistoryEntry) =>
  entry.from && entry.to && entry.from !== entry.to
    ? `${entry.from} → ${entry.to}`
    : (entry.to ?? entry.from ?? '')
</script>

<template>
  <PageContainer>
    <el-alert v-if="loadError && !status" type="error" :closable="false" show-icon>
      <template #title>读取升级状态失败：{{ loadError }}</template>
      <el-button size="small" @click="load">重试</el-button>
    </el-alert>
    <el-skeleton v-else-if="!status" :rows="6" animated />

    <div v-else class="upgrade">
      <el-alert
        v-if="!status.enabled"
        type="warning"
        :closable="false"
        show-icon
        title="升级系统未启用"
      >
        在
        <RouterLink :to="{ name: 'settings' }">设置 › 宿主配置 › 升级</RouterLink>
        中开启后，可以在这里检查更新、升级和回滚。
      </el-alert>

      <el-alert v-if="outcome" :type="outcome.type" show-icon @close="outcome = null">
        <template #title>{{ outcome.text }}</template>
      </el-alert>

      <el-alert v-if="job" :type="jobType" :closable="false" show-icon class="job">
        <template #title>
          <span class="job-title">
            <span
              v-if="isActive(job) || offlineSince !== null"
              class="spinner"
              aria-hidden="true"
            />
            {{ jobTitle }}
          </span>
        </template>
        <p v-if="offlineSince !== null">
          面板暂时无法连接，恢复后会自动刷新。
          <template v-if="slow">已经等了较长时间，请检查宿主进程和日志。</template>
        </p>
        <p v-else-if="job.message">{{ job.message }}</p>
        <p v-else-if="job.state === 'running'">期间不能安装或更新插件，也不能重启。</p>
        <template v-if="incompatible.length">
          <ul class="incompatible">
            <li v-for="[plugin, reason] in incompatible" :key="plugin">
              <div class="incompatible-head">
                <strong class="tk-mono">{{ plugin === '*' ? '插件组合' : plugin }}</strong>
                <el-select
                  v-if="plugin !== '*'"
                  v-model="choices[plugin]"
                  size="small"
                  class="choice"
                  :aria-label="`${plugin} 的处理方式`"
                  :disabled="busy"
                >
                  <el-option value="suspend" label="停用，之后再处理" />
                  <el-option
                    v-for="(version, index) in versionsOf(plugin)"
                    :key="version"
                    :value="version"
                    :label="`升级到 ${version}${index === 0 ? '（改动最小）' : ''}`"
                  />
                </el-select>
              </div>
              <span class="reason">{{ reason }}</span>
            </li>
          </ul>
          <p v-if="comboConflict">插件之间的依赖冲突需要手动处理：先卸载其中一部分插件再升级。</p>
          <template v-else>
            <p class="choice-note">
              停用的插件保留记录、数据和配置，宿主照常升级；之后可在待处理里装上兼容版本恢复。
            </p>
            <el-button size="small" type="warning" :disabled="busy" @click="start('upgrade', true)">
              按所选方式处理后升级
            </el-button>
          </template>
        </template>
      </el-alert>

      <PagePanel title="当前版本">
        <div class="current">
          <div>
            <div class="version tk-mono">{{ status.current_version }}</div>
            <dl class="facts">
              <div>
                <dt>通道</dt>
                <dd>{{ CHANNELS[status.channel] ?? status.channel }}</dd>
              </div>
              <div>
                <dt>自动化</dt>
                <dd>
                  {{ POLICIES[status.policy] ?? status.policy }}，每
                  {{ status.check_interval_hours }} 小时
                </dd>
              </div>
              <div v-if="status.previous_version">
                <dt>上一个版本</dt>
                <dd class="tk-mono">{{ status.previous_version }}</dd>
              </div>
              <div v-if="status.release_url">
                <dt>发布说明</dt>
                <dd>
                  <a :href="status.release_url" target="_blank" rel="noopener noreferrer"
                    >在 GitHub 查看</a
                  >
                </dd>
              </div>
            </dl>
          </div>
          <div class="actions">
            <el-button
              type="primary"
              :loading="checking"
              :disabled="!status.enabled || busy"
              @click="check"
            >
              检查更新
            </el-button>
            <el-button
              v-if="status.previous_version"
              :disabled="!status.enabled || busy"
              @click="start('rollback')"
            >
              回滚到 {{ status.previous_version }}
            </el-button>
          </div>
        </div>
      </PagePanel>

      <PagePanel v-if="status.last_check" title="可用更新">
        <template #extra>检查于 {{ relativeText(status.last_check.checked_at) }}</template>
        <div v-if="candidate" class="candidate">
          <div class="candidate-head">
            <strong class="tk-mono">{{ candidate.tag }}</strong>
            <StatusTag v-if="candidate.prerelease" kind="pending">预发布</StatusTag>
            <span class="muted">来源：{{ candidate.source }}</span>
            <el-button
              type="primary"
              class="upgrade-button"
              :disabled="!status.enabled || busy"
              @click="start('upgrade')"
            >
              升级到 {{ candidate.tag }}
            </el-button>
          </div>
          <div v-if="changelog.length" class="changelog">
            <p class="muted">
              {{
                changelog.length > 1
                  ? `从 ${status.current_version} 升级会带来以下 ${changelog.length} 个版本的变化：`
                  : '更新内容：'
              }}
            </p>
            <details v-for="(note, index) in changelog" :key="note.tag" :open="index === 0">
              <summary>
                <strong class="tk-mono">{{ note.tag }}</strong>
                <StatusTag v-if="note.prerelease" kind="pending">预发布</StatusTag>
                <time v-if="note.published_at" class="muted" :title="timeText(note.published_at)">
                  {{ relativeText(note.published_at) }}
                </time>
                <a
                  v-if="note.url"
                  class="release-link"
                  :href="note.url"
                  target="_blank"
                  rel="noopener noreferrer"
                  @click.stop
                  >发布页</a
                >
              </summary>
              <!-- renderMarkdown 先转义全部原文，只输出自己生成的标签 -->
              <!-- eslint-disable-next-line vue/no-v-html -->
              <div v-if="note.body.trim()" class="notes" v-html="renderMarkdown(note.body)" />
              <p v-else class="muted">这个版本没有写发布说明。</p>
            </details>
          </div>
          <!-- eslint-disable-next-line vue/no-v-html -->
          <div v-else-if="candidate.notes" class="notes" v-html="renderMarkdown(candidate.notes)" />
          <p v-if="status.last_check.changelog_error" class="muted">
            无法读取发布说明：{{ status.last_check.changelog_error }}
          </p>
        </div>
        <p v-else class="muted">当前已是最新版本</p>
      </PagePanel>

      <PagePanel title="升级记录">
        <el-empty v-if="!status.history.length" :image-size="48" description="还没有升级记录" />
        <ol v-else class="history">
          <li v-for="(entry, index) in status.history" :key="index">
            <StatusTag :kind="historyTag(entry)[1]">{{ historyTag(entry)[0] }}</StatusTag>
            <span class="tk-mono">{{ versions(entry) }}</span>
            <time class="muted">{{ timeText(entry.timestamp) }}</time>
            <p v-if="entry.error" class="history-error">{{ entry.error }}</p>
          </li>
        </ol>
      </PagePanel>
    </div>
  </PageContainer>
</template>

<style scoped>
.upgrade {
  display: grid;
  gap: 16px;
}
.muted {
  color: var(--tk-muted);
  font-size: 12px;
}
.job p {
  margin: 4px 0 0;
}
.job-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.spinner {
  width: 12px;
  height: 12px;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.incompatible {
  display: grid;
  gap: 10px;
  margin: 8px 0;
  padding: 0;
  list-style: none;
}
.incompatible-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
}
.incompatible .choice {
  width: 220px;
}
.incompatible .reason {
  display: block;
  margin-top: 2px;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.choice-note {
  margin: 0 0 8px;
  font-size: 12px;
}
.current {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}
.version {
  color: var(--tk-heading);
  font-size: 28px;
  font-weight: 700;
  line-height: 1.2;
}
.facts {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 24px;
  margin: 8px 0 0;
  font-size: 13px;
}
.facts div {
  display: flex;
  gap: 6px;
}
.facts dt {
  color: var(--tk-muted);
}
.facts dd {
  margin: 0;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.actions .el-button + .el-button {
  margin-left: 0;
}
.candidate-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 10px;
}
.candidate-head strong {
  color: var(--tk-heading);
  font-size: 16px;
}
.upgrade-button {
  margin-left: auto;
}
.changelog {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}
.changelog > p {
  margin: 0;
}
.changelog details {
  border: 1px solid var(--tk-line);
  border-radius: 8px;
}
.changelog summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  padding: 10px 14px;
  cursor: pointer;
}
.changelog summary strong {
  color: var(--tk-heading);
}
.release-link {
  margin-left: auto;
  font-size: 12px;
}
.changelog .notes {
  margin: 0;
  border-top: 1px solid var(--tk-line);
  border-radius: 0 0 8px 8px;
}
.changelog details > .muted {
  margin: 0;
  padding: 0 14px 12px;
}
.notes {
  max-height: 420px;
  margin: 12px 0 0;
  padding: 4px 14px 12px;
  overflow: auto;
  border-radius: 8px;
  background: var(--tk-surface-2);
  color: var(--tk-text);
  font-size: 13px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.notes :deep(h3),
.notes :deep(h4),
.notes :deep(h5),
.notes :deep(h6) {
  margin: 14px 0 6px;
  color: var(--tk-heading);
  font-size: 14px;
}
.notes :deep(p) {
  margin: 8px 0;
}
.notes :deep(ul),
.notes :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
}
.notes :deep(code) {
  padding: 0 4px;
  border-radius: 4px;
  background: rgba(127, 127, 127, 0.15);
  font-family: var(--tk-font-mono);
  font-size: 12px;
}
.notes :deep(pre) {
  overflow: auto;
}
.history {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.history li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 10px;
  font-size: 13px;
}
.history-error {
  flex-basis: 100%;
  margin: 0;
  color: var(--el-color-danger);
  font-size: 12px;
  overflow-wrap: anywhere;
}
</style>
