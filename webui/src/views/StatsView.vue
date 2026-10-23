<script setup lang="ts">
import { computed, onMounted, ref, shallowRef, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  api,
  ApiError,
  type StatsData,
  type StatsDays,
  type StatsFailure,
  type StatsPlugin,
  type StatsSummary,
} from '@/api'
import PageContainer from '@/components/PageContainer.vue'
import PagePanel from '@/components/PagePanel.vue'
import { confirmAction } from '@/composables/confirm'
import { numberText, relativeText, timeText } from '@/composables/format'

const PERIODS = [
  { label: '24 小时', value: 1 },
  { label: '7 天', value: 7 },
  { label: '30 天', value: 30 },
]

const days = ref<StatsDays>(1)
const data = shallowRef<StatsData | null>(null)
const loadError = ref<string | null>(null)
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    data.value = await api.manage.stats(days.value)
    loadError.value = null
  } catch (error) {
    loadError.value = error instanceof ApiError ? error.message : String(error)
  } finally {
    loading.value = false
  }
}
onMounted(load)
watch(days, load)

const msText = (value: number | null) =>
  value === null ? '—' : value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(1)} s`
const rateText = (value: number | null) =>
  value === null ? '—' : `${Math.round(value * 1000) / 10}%`
// 成功率低于 95% 标红：多半是插件出错，值得点开看。
const rateClass = (value: number | null) => (value !== null && value < 0.95 ? 'bad' : '')
const topReason = (item: StatsSummary) => item.reasons[0]?.label ?? ''
const actionsText = (item: StatsSummary, limit = 2) =>
  Object.entries(item.actions)
    .slice(0, limit)
    .map(([name, count]) => `${name} ${count}`)
    .join(' · ')

const total = computed(() => data.value?.total ?? null)
const maxCalls = computed(() => Math.max(1, ...(data.value?.plugins ?? []).map((p) => p.calls)))
const share = (item: StatsPlugin) => `${(item.calls / maxCalls.value) * 100}%`

const trend = computed(() => data.value?.trend ?? [])
const peak = computed(() => Math.max(0, ...trend.value))
const unit = computed(() => (data.value?.trend_unit === 'day' ? '天' : '小时'))
// 第 index 根柱子的起点时间，用于提示。
function slotTitle(index: number, value: number) {
  const since = data.value ? new Date(data.value.since) : new Date()
  const step = data.value?.trend_unit === 'day' ? 86400_000 : 3600_000
  const start = new Date(since.getTime() + index * step)
  const label =
    data.value?.trend_unit === 'day'
      ? start.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
      : start.toLocaleString('zh-CN', {
          hour12: false,
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
        }) + ' 时'
  return `${label}：${value} 次`
}

const router = useRouter()
const replaying = ref<number | null>(null)
const whereText = (f: StatsFailure) =>
  f.chat === 'group' ? `群 ${f.group_name || f.group_id}` : '私聊'

async function replay(failure: StatsFailure) {
  const ok = await confirmAction(
    `将以 Master 身份在沙盒${failure.chat === 'group' ? '群聊' : '私聊'}执行「${failure.preview || '原消息'}」` +
      `（原用户 ${failure.user_id}，${whereText(failure)}）。插件读写的是数据副本，真实账号的发送会被拦截。`,
    '在沙盒重放',
    '重放',
  )
  if (!ok) return
  replaying.value = failure.id
  try {
    const { chat } = await api.manage.statsReplay(failure.id)
    await router.push({ name: 'sandbox', query: { chat } })
  } catch (error) {
    ElMessage.error(`重放失败：${error instanceof ApiError ? error.message : String(error)}`)
  } finally {
    replaying.value = null
  }
}

const shortKind = (kind: string) => kind.split('.').pop() ?? kind
const shortLocation = (location: string) => {
  const [file, line] = [location.slice(0, location.lastIndexOf(':')), location.split(':').pop()]
  const parts = file.split(/[\\/]/)
  return `${parts.slice(-2).join('/')}:${line}`
}
</script>

<template>
  <PageContainer>
    <PagePanel title="概览">
      <template #extra>
        <el-segmented v-model="days" :options="PERIODS" size="small" aria-label="时间范围" />
      </template>
      <el-alert v-if="loadError && !data" type="error" :closable="false" show-icon>
        <template #title>读取统计失败：{{ loadError }}</template>
        <el-button size="small" @click="load">重试</el-button>
      </el-alert>
      <el-skeleton v-else-if="!data" :rows="3" animated />
      <template v-else>
        <div class="kpis" :class="{ dim: loading }">
          <div class="kpi">
            <span class="label">调用</span><b>{{ numberText(total?.calls ?? 0) }}</b>
          </div>
          <div class="kpi">
            <span class="label">成功率</span>
            <b :class="rateClass(total?.success_rate ?? null)">{{
              rateText(total?.success_rate ?? null)
            }}</b>
          </div>
          <div class="kpi">
            <span class="label">平均耗时</span><b>{{ msText(total?.avg_ms ?? null) }}</b>
          </div>
          <div class="kpi">
            <span class="label">被拒</span><b>{{ numberText(total?.denied ?? 0) }}</b>
            <span v-if="total?.reasons.length" class="hint">多为{{ topReason(total) }}</span>
          </div>
          <div class="kpi">
            <span class="label">报错</span>
            <b :class="{ bad: total?.errors }">{{ numberText(total?.errors ?? 0) }}</b>
          </div>
        </div>
        <figure class="trend" :aria-label="`每${unit}调用量`">
          <div class="bars">
            <i
              v-for="(value, index) in trend"
              :key="index"
              :style="{ height: peak ? `${(value / peak) * 100}%` : '0' }"
              :title="slotTitle(index, value)"
            />
          </div>
          <figcaption>
            <span>{{ trend.length }} {{ unit }}前</span>
            <span>每{{ unit }}最多 {{ numberText(peak) }} 次</span>
            <span>现在</span>
          </figcaption>
        </figure>
      </template>
    </PagePanel>

    <PagePanel v-if="data" title="插件">
      <el-table :data="data.plugins" row-key="key" class="plugins" :class="{ dim: loading }">
        <template #empty>这段时间还没有调用记录</template>
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="detail">
              <section>
                <h4>命令与监听</h4>
                <table class="mini">
                  <thead>
                    <tr>
                      <th>名称</th>
                      <th>调用</th>
                      <th>成功率</th>
                      <th>平均耗时</th>
                      <th>被拒</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="item in (row as StatsPlugin).commands" :key="item.key">
                      <td class="tk-mono">{{ item.key }}</td>
                      <td>{{ numberText(item.calls) }}</td>
                      <td :class="rateClass(item.success_rate)">
                        {{ rateText(item.success_rate) }}
                      </td>
                      <td>{{ msText(item.avg_ms) }}</td>
                      <td>
                        {{ item.denied || '—' }}
                        <span v-if="item.denied" class="sub">{{ topReason(item) }}</span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </section>
              <section>
                <h4>调用最多的群</h4>
                <table class="mini">
                  <thead>
                    <tr>
                      <th>群</th>
                      <th>调用</th>
                      <th>成功率</th>
                      <th>被拒</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="group in (row as StatsPlugin).groups" :key="group.key">
                      <td>
                        {{ group.name || group.key }}
                        <span v-if="group.name && group.key" class="sub tk-mono">{{
                          group.key
                        }}</span>
                      </td>
                      <td>{{ numberText(group.calls) }}</td>
                      <td :class="rateClass(group.success_rate)">
                        {{ rateText(group.success_rate) }}
                      </td>
                      <td>
                        {{ group.denied || '—' }}
                        <span v-if="group.denied" class="sub">{{ topReason(group) }}</span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </section>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="插件" min-width="160">
          <template #default="{ row }">
            <strong class="name">{{ row.label }}</strong>
            <span v-if="row.label !== row.key" class="sub tk-mono">{{ row.key }}</span>
          </template>
        </el-table-column>
        <el-table-column label="调用" min-width="140">
          <template #default="{ row }">
            <div class="calls">
              <span>{{ numberText(row.calls) }}</span>
              <i class="share"><i :style="{ width: share(row as StatsPlugin) }" /></i>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="成功率" width="90" align="right">
          <template #default="{ row }">
            <span :class="rateClass(row.success_rate)">{{ rateText(row.success_rate) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="耗时" min-width="130" align="right">
          <template #default="{ row }">
            {{ msText(row.avg_ms) }}
            <span
              class="sub"
              :title="row.avg_render_ms ? `其中渲染平均 ${msText(row.avg_render_ms)}` : undefined"
            >
              最长 {{ msText(row.max_ms || null) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="被拒" min-width="110">
          <template #default="{ row }">
            {{ row.denied || '—' }}
            <span v-if="row.denied" class="sub">{{ topReason(row as StatsPlugin) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="平台动作" min-width="150">
          <template #default="{ row }">
            <span class="sub">{{ actionsText(row as StatsPlugin) || '—' }}</span>
          </template>
        </el-table-column>
      </el-table>
      <p v-if="data.idle.length" class="idle">这段时间没有调用：{{ data.idle.join('、') }}</p>
      <p class="foot">
        命令每次命中都记一次；被动监听（如复读）只在回复、做了平台动作或报错时记。不记参数、用户和消息内容。
      </p>
    </PagePanel>

    <PagePanel v-if="data" title="最近的错误">
      <template #extra>
        <span class="sub">同一错误只报告一次，之后每小时汇总</span>
      </template>
      <el-empty v-if="!data.errors.length" :image-size="56" description="启动以来没有报错" />
      <ul v-else class="errors">
        <li v-for="error in data.errors" :key="error.fingerprint" class="error">
          <div class="head">
            <strong class="tk-mono">{{ shortKind(error.kind) }}</strong>
            <span class="tk-mono sub" :title="error.location">{{
              shortLocation(error.location)
            }}</span>
            <span class="count">{{ numberText(error.total) }} 次</span>
          </div>
          <p class="message">{{ error.message || '（没有错误消息）' }}</p>
          <p class="sub">
            首次
            <span :title="timeText(error.first_seen)">{{ relativeText(error.first_seen) }}</span> ·
            最近 <span :title="timeText(error.last_seen)">{{ relativeText(error.last_seen) }}</span>
            <template v-if="error.groups.length">
              · 涉及
              <span :title="error.groups.join('、')">{{ error.groups.length }} 个群</span>
            </template>
          </p>
          <ul v-if="error.failures.length" class="inputs">
            <li v-for="failure in error.failures" :key="failure.id">
              <span class="input">{{ failure.preview || '（非文本消息）' }}</span>
              <span class="sub">{{ whereText(failure) }} · {{ relativeText(failure.at) }}</span>
              <el-button
                size="small"
                text
                type="primary"
                :loading="replaying === failure.id"
                @click="replay(failure)"
              >
                在沙盒重放
              </el-button>
            </li>
          </ul>
          <details>
            <summary>最近一次的调用栈</summary>
            <pre class="tk-mono">{{ error.traceback }}</pre>
          </details>
        </li>
      </ul>
    </PagePanel>
  </PageContainer>
</template>

<style scoped>
.dim {
  opacity: 0.6;
  transition: opacity 0.2s;
}
.sub {
  margin-left: 6px;
  color: var(--tk-muted);
  font-size: 12px;
}
.bad {
  color: var(--el-color-danger);
}
.kpis {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
}
.kpi {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 12px 14px;
  border: 1px solid var(--tk-line);
  border-radius: 10px;
}
.kpi .label {
  color: var(--tk-muted);
  font-size: 12px;
}
.kpi b {
  color: var(--tk-heading);
  font-size: 24px;
  font-variant-numeric: tabular-nums;
}
.kpi b.bad {
  color: var(--el-color-danger);
}
.kpi .hint {
  color: var(--tk-muted);
  font-size: 12px;
}
.trend {
  margin: 18px 0 0;
}
.bars {
  display: flex;
  align-items: flex-end;
  gap: 2px;
  height: 96px;
  border-bottom: 1px solid var(--tk-line);
}
.bars i {
  flex: 1;
  min-height: 1px;
  border-radius: 2px 2px 0 0;
  background: var(--el-color-primary);
  opacity: 0.75;
}
.bars i:hover {
  opacity: 1;
}
.trend figcaption {
  display: flex;
  justify-content: space-between;
  margin-top: 6px;
  color: var(--tk-muted);
  font-size: 12px;
}
.name {
  color: var(--tk-heading);
}
.calls {
  display: flex;
  align-items: center;
  gap: 10px;
  font-variant-numeric: tabular-nums;
}
.calls > span {
  min-width: 3em;
}
.share {
  flex: 1;
  height: 6px;
  border-radius: 3px;
  background: var(--tk-line);
  overflow: hidden;
}
.share i {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: var(--el-color-primary);
}
.detail {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
  padding: 4px 16px 8px 48px;
}
.detail h4 {
  margin: 0 0 6px;
  color: var(--tk-heading);
  font-size: 13px;
}
.mini {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.mini th {
  color: var(--tk-muted);
  font-weight: 500;
  font-size: 12px;
  text-align: left;
}
.mini th,
.mini td {
  padding: 5px 8px 5px 0;
  border-bottom: 1px solid var(--tk-line);
}
.idle,
.foot {
  margin: 12px 0 0;
  color: var(--tk-muted);
  font-size: 12px;
  line-height: 1.6;
}
.idle {
  color: var(--tk-text);
}
.errors {
  margin: 0;
  padding: 0;
  list-style: none;
}
.error {
  padding: 12px 0;
  border-top: 1px solid var(--tk-line);
}
.error:first-child {
  border-top: 0;
  padding-top: 0;
}
.error .head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 8px;
}
.error .head strong {
  color: var(--el-color-danger);
}
.error .count {
  margin-left: auto;
  color: var(--tk-heading);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.message {
  margin: 4px 0;
  color: var(--tk-heading);
  overflow-wrap: anywhere;
}
.error p.sub {
  margin: 0;
}
.inputs {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
}
.inputs li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px 8px;
  padding: 2px 0;
}
.input {
  max-width: 100%;
  padding: 1px 8px;
  border-radius: 6px;
  background: rgba(127, 127, 127, 0.1);
  font-size: 13px;
  overflow-wrap: anywhere;
}
.inputs .sub {
  margin-left: 0;
}
details summary {
  margin-top: 6px;
  color: var(--tk-muted);
  font-size: 12px;
  cursor: pointer;
}
pre {
  max-height: 320px;
  margin: 6px 0 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(127, 127, 127, 0.08);
  overflow: auto;
  font-size: 12px;
  line-height: 1.5;
}
@media (max-width: 900px) {
  .kpis {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .detail {
    grid-template-columns: minmax(0, 1fr);
    padding-left: 16px;
  }
}
@media (max-width: 560px) {
  .kpis {
    gap: 8px;
  }
  .kpi {
    padding: 8px 10px;
  }
  .kpi b {
    font-size: 18px;
  }
  .mini td.tk-mono {
    overflow-wrap: anywhere;
  }
}
</style>
