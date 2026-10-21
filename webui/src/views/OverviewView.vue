<script setup lang="ts">
import { computed } from 'vue'
import PageContainer from '@/components/PageContainer.vue'
import PagePanel from '@/components/PagePanel.vue'
import ResourceBoundary from '@/components/ResourceBoundary.vue'
import StatusTag from '@/components/StatusTag.vue'
import {
  accountStatus,
  isInactiveExternal,
  pluginStats,
  type StatusKind,
} from '@/composables/status'
import { bytesText, numberText, platformText, uptimeText } from '@/composables/format'
import { usePanelData } from '@/composables/usePanelData'

const { overview, accounts, features } = usePanelData()

const accountList = computed(() => accounts.data.value?.accounts ?? null)
const featureList = computed(() => features.data.value?.features ?? null)
const activeFeatures = computed(() =>
  featureList.value?.filter((item) => !isInactiveExternal(item)),
)
const stats = computed(() => (activeFeatures.value ? pluginStats(activeFeatures.value) : null))

const perMinute = computed(() => {
  const messages = overview.data.value?.messages
  return messages ? messages.received_rate + messages.sent_rate : null
})

// 插件分段条：运行 / 维护 / 停用 / 未加载，互斥且合计为插件总数。
const segments = computed(() => {
  const s = stats.value
  if (!s) return []
  return [
    { key: 'run', label: '运行', count: s.running, color: 'var(--tk-ok-solid)' },
    { key: 'maint', label: '维护', count: s.maintenance, color: 'var(--tk-gold)' },
    { key: 'off', label: '停用', count: s.disabled, color: 'var(--tk-switch-off)' },
    { key: 'unloaded', label: '未加载', count: s.unloaded, color: 'var(--tk-coral)' },
  ]
})

// 需要关注的插件：未加载 > 维护中 > 已停用。
const abnormalPlugins = computed(() => {
  const rows: { key: string; name: string; label: string; kind: StatusKind }[] = []
  const list = activeFeatures.value ?? []
  const add = (
    label: string,
    kind: StatusKind,
    match: (item: (typeof list)[number]) => boolean,
  ) => {
    for (const item of list.filter(match))
      rows.push({ key: item.plugin, name: item.name || item.plugin, label, kind })
  }
  add('未加载', 'danger', (item) => !item.loaded)
  add('维护中', 'pending', (item) => item.loaded && item.enabled && item.maintenance)
  add('已停用', 'off', (item) => item.loaded && !item.enabled)
  return rows
})

interface AttentionItem {
  kind: StatusKind
  text: string
  to?: string
  action?: string
}
const attention = computed<AttentionItem[]>(() => {
  const items: AttentionItem[] = []
  for (const account of accountList.value ?? []) {
    if (!account.online)
      items.push({
        kind: 'danger',
        text: `账号 ${account.id} 离线`,
        to: '/accounts',
        action: '查看',
      })
  }
  for (const feature of activeFeatures.value ?? []) {
    const name = feature.name || feature.plugin
    if (!feature.loaded)
      items.push({ kind: 'pending', text: `${name} 未加载`, to: '/plugins', action: '处理' })
    else if (feature.maintenance)
      items.push({ kind: 'pending', text: `${name} 处于维护状态`, to: '/plugins', action: '处理' })
  }
  const failed: Array<[string, { error: { value: unknown } }]> = [
    ['总览', overview],
    ['账户', accounts],
    ['插件', features],
  ]
  for (const [label, source] of failed) {
    if (source.error.value) items.push({ kind: 'danger', text: `${label}数据读取失败` })
  }
  return items
})

// ---------------- 近一小时消息活动 ----------------
const CHART_H = 150
const BUCKET = 1000 / 60
const activity = computed(() => overview.data.value?.activity ?? [])
const quiet = computed(() => activity.value.every(([received, sent]) => !received && !sent))
const bars = computed(() => {
  const top = Math.max(1, ...activity.value.map(([received, sent]) => received + sent))
  const scale = (CHART_H - 8) / top
  const height = (count: number) => (count ? Math.max(count * scale, 2) : 0)
  return activity.value.map(([received, sent], index) => {
    const sentH = height(sent)
    const receivedH = height(received)
    const ago = activity.value.length - 1 - index
    return {
      x: index * BUCKET + 2,
      sentH,
      sentY: CHART_H - sentH,
      receivedH,
      receivedY: CHART_H - sentH - receivedH,
      last: ago === 0,
      title: `${ago ? `${ago} 分钟前` : '当前分钟'}：接收 ${received}，发送 ${sent}`,
    }
  })
})

function percent(value: number) {
  return `${Math.min(Math.max(value, 0), 100)}%`
}
</script>

<template>
  <PageContainer>
    <ResourceBoundary :resource="overview" label="运行数据">
      <template #default="{ data }">
        <section class="hero" aria-label="运行状态">
          <div class="hero-main">
            <div class="state">
              <span class="pulse" />运行中{{ data.version ? ` · v${data.version}` : '' }}
            </div>
            <h2>{{ attention.length ? `${attention.length} 项待处理` : '运行正常' }}</h2>
            <div class="sub">
              已运行 <b>{{ uptimeText(data.uptime_seconds) }}</b>
            </div>
            <div class="hero-stats">
              <div>
                <b
                  >{{ numberText(data.online_accounts)
                  }}<small v-if="accountList"> / {{ numberText(accountList.length) }}</small></b
                >在线账号
              </div>
              <div>
                <b>{{ numberText(data.active_groups) }}</b
                >活动群组
              </div>
              <div>
                <b
                  >{{ stats ? numberText(stats.running) : '—'
                  }}<small v-if="stats"> / {{ numberText(stats.total) }}</small></b
                >运行插件
              </div>
              <div>
                <b>{{ numberText(perMinute) }}</b
                >条/分钟
              </div>
            </div>
          </div>
          <ul v-if="attention.length" class="alerts">
            <li v-for="(item, index) in attention.slice(0, 4)" :key="index">
              <span class="dot" :class="`is-${item.kind}`" aria-hidden="true" />
              <span class="alert-text">{{ item.text }}</span>
              <router-link v-if="item.to" :to="item.to">{{ item.action }} →</router-link>
            </li>
          </ul>
        </section>

        <PagePanel class="activity">
          <div class="chart-head">
            <div>
              <h3>消息活动</h3>
              <div class="sub">近 60 分钟，每分钟一格</div>
            </div>
            <div class="kpi">
              <b>{{ numberText(data.messages.received_count) }}</b
              >累计接收
            </div>
            <div class="kpi">
              <b>{{ numberText(data.messages.sent_count) }}</b
              >累计发送
            </div>
            <div class="legend">
              <span><i class="recv" />接收</span>
              <span><i class="sent" />发送</span>
            </div>
          </div>
          <div class="chart-wrap">
            <svg
              class="chart"
              :viewBox="`0 0 1000 ${CHART_H}`"
              preserveAspectRatio="none"
              role="img"
              aria-label="近 60 分钟每分钟的收发消息数"
            >
              <template v-if="quiet">
                <rect
                  v-for="bar in bars"
                  :key="bar.x"
                  :x="bar.x"
                  :y="CHART_H - 2"
                  :width="BUCKET - 4"
                  height="2"
                  rx="1"
                  class="idle"
                />
              </template>
              <template v-else>
                <g v-for="bar in bars" :key="bar.x">
                  <title>{{ bar.title }}</title>
                  <rect
                    v-if="bar.receivedH"
                    :x="bar.x"
                    :y="bar.receivedY"
                    :width="BUCKET - 4"
                    :height="bar.receivedH"
                    rx="2"
                    class="recv"
                    :opacity="bar.last ? 1 : 0.78"
                  />
                  <rect
                    v-if="bar.sentH"
                    :x="bar.x"
                    :y="bar.sentY"
                    :width="BUCKET - 4"
                    :height="bar.sentH"
                    rx="1.5"
                    class="sent"
                  />
                </g>
              </template>
            </svg>
            <div v-if="quiet" class="quiet">近一小时没有消息</div>
          </div>
          <div class="axis" aria-hidden="true">
            <span>-60 分钟</span><span>-45</span><span>-30</span><span>-15</span><span>现在</span>
          </div>
        </PagePanel>
      </template>
    </ResourceBoundary>

    <div class="row">
      <PagePanel title="账号">
        <template v-if="accountList" #extra>
          <router-link class="tk-link" to="/accounts">管理 →</router-link>
        </template>
        <ResourceBoundary :resource="accounts" label="账户数据">
          <template #default="{ data }">
            <el-empty
              v-if="!data.accounts.length"
              :image-size="48"
              description="尚无账户。前往“账户”页配对接入。"
            />
            <ul v-else class="account-list">
              <li
                v-for="account in data.accounts.slice(0, 6)"
                :key="`${account.platform}/${account.id}`"
              >
                <span class="dot" :class="account.online ? 'is-on' : 'is-off'" aria-hidden="true" />
                <div>
                  <span class="account-id">{{ account.id }}</span>
                  <div class="meta">
                    {{ platformText(account.platform) }} ·
                    {{ numberText(account.group_count) }} 个群
                  </div>
                </div>
                <StatusTag :kind="accountStatus(account).kind">{{
                  accountStatus(account).label
                }}</StatusTag>
              </li>
            </ul>
            <p v-if="data.accounts.length > 6" class="note">仅显示前 6 个账户。</p>
          </template>
        </ResourceBoundary>
      </PagePanel>

      <PagePanel title="插件">
        <template #extra><router-link class="tk-link" to="/plugins">查看 →</router-link></template>
        <ResourceBoundary :resource="features" label="插件数据">
          <template #default>
            <template v-if="stats">
              <div class="plugin-big">
                <b>{{ stats.running }}</b
                ><span>/ {{ stats.total }} 运行中</span>
              </div>
              <div
                class="seg-bar"
                role="img"
                :aria-label="segments.map((item) => `${item.label} ${item.count}`).join('，')"
              >
                <i
                  v-for="item in segments.filter((segment) => segment.count)"
                  :key="item.key"
                  :style="{ flex: item.count, background: item.color }"
                />
              </div>
              <ul v-if="abnormalPlugins.length" class="plugin-list">
                <li v-for="item in abnormalPlugins.slice(0, 5)" :key="item.key">
                  <span class="dot" :class="`is-${item.kind}`" aria-hidden="true" />
                  <span class="plugin-name">{{ item.name }}</span>
                  <span class="plugin-state">{{ item.label }}</span>
                </li>
              </ul>
              <p v-else class="all-ok">全部正常</p>
              <p v-if="abnormalPlugins.length > 5" class="note">
                另有 {{ abnormalPlugins.length - 5 }} 项未列出。
              </p>
            </template>
          </template>
        </ResourceBoundary>
      </PagePanel>

      <PagePanel title="主机">
        <template v-if="overview.data.value">
          <template v-if="overview.data.value.resources">
            <div class="res">
              <div class="res-head">
                CPU<span
                  ><b>{{ Math.round(overview.data.value.resources.cpu_percent) }}</b
                  >%</span
                >
              </div>
              <div class="track">
                <i :style="{ width: percent(overview.data.value.resources.cpu_percent) }" />
              </div>
            </div>
            <div class="res">
              <div class="res-head">
                内存<span
                  ><b>{{ Math.round(overview.data.value.resources.memory_percent) }}</b
                  >% · {{ bytesText(overview.data.value.resources.memory_used) }} /
                  {{ bytesText(overview.data.value.resources.memory_total) }}</span
                >
              </div>
              <div class="track">
                <i :style="{ width: percent(overview.data.value.resources.memory_percent) }" />
              </div>
            </div>
            <div class="res">
              <div class="res-head">
                磁盘<span
                  ><b>{{ Math.round(overview.data.value.resources.disk_percent) }}</b
                  >% · {{ bytesText(overview.data.value.resources.disk_used) }} /
                  {{ bytesText(overview.data.value.resources.disk_total) }}</span
                >
              </div>
              <div class="track">
                <i :style="{ width: percent(overview.data.value.resources.disk_percent) }" />
              </div>
            </div>
          </template>
          <p v-else class="note first">未安装 psutil，无法读取主机资源。</p>
          <div v-if="overview.data.value.rss != null" class="res">
            <div class="res-head">
              Tenko 进程<span
                ><b>{{ bytesText(overview.data.value.rss) }}</b></span
              >
            </div>
          </div>
        </template>
        <p v-else class="note first">数据读取完成后显示。</p>
      </PagePanel>
    </div>
  </PageContainer>
</template>

<style scoped>
/* ---------------- 顶部状态卡：两个主题下都是深色毛玻璃 ---------------- */
.hero {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 300px);
  align-items: start;
  gap: 24px 32px;
  isolation: isolate;
  overflow: hidden;
  padding: 28px 32px 26px;
  border-radius: 18px;
  background: #131a33;
  color: #fff;
}
.hero::before {
  position: absolute;
  inset: -30%;
  z-index: -2;
  background: url('@/assets/tenshi.webp') 70% 30% / cover;
  filter: blur(70px) saturate(1.5) brightness(0.8);
  content: '';
}
.hero::after {
  position: absolute;
  inset: 0;
  z-index: -1;
  background: linear-gradient(
    90deg,
    rgba(14, 18, 40, 0.72) 0,
    rgba(14, 18, 40, 0.35) 60%,
    rgba(14, 18, 40, 0.15) 100%
  );
  content: '';
}
.state {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  letter-spacing: 0.12em;
  opacity: 0.85;
}
.pulse {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #6fe0a0;
  box-shadow: 0 0 0 5px rgba(111, 224, 160, 0.18);
}
.hero h2 {
  margin: 10px 0 6px;
  font-size: 40px;
  font-weight: 900;
  letter-spacing: 0.03em;
  line-height: 1.15;
}
.hero .sub {
  font-size: 14px;
  opacity: 0.78;
}
.hero .sub b {
  font-weight: 700;
}
.hero-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 16px 0;
  margin-top: 28px;
}
.hero-stats > div {
  padding: 0 30px;
  border-left: 1px solid rgba(255, 255, 255, 0.18);
  font-size: 12.5px;
  letter-spacing: 0.08em;
  opacity: 0.9;
}
.hero-stats > div:first-child {
  padding-left: 0;
  border-left: 0;
}
.hero-stats b {
  display: block;
  font-family: var(--tk-font-num);
  font-size: 38px;
  font-weight: 700;
  letter-spacing: 0.01em;
  line-height: 1.1;
}
.hero-stats b small {
  font-size: 20px;
  opacity: 0.6;
}
.alerts {
  display: grid;
  margin: 0;
  padding: 0;
  gap: 8px;
  list-style: none;
}
.alerts li {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 11px;
  background: rgba(255, 255, 255, 0.1);
  backdrop-filter: blur(6px);
  font-size: 13px;
}
.alerts .dot {
  width: 7px;
  height: 7px;
  flex: none;
  border-radius: 50%;
}
.alerts .dot.is-danger {
  background: #ff8c84;
}
.alerts .dot.is-pending {
  background: #f0c56a;
}
.alert-text {
  min-width: 0;
  overflow-wrap: anywhere;
}
.alerts a {
  margin-left: auto;
  color: #b9cdff;
  font-size: 12px;
  white-space: nowrap;
}

/* ---------------- 消息活动 ---------------- */
.activity {
  --el-card-padding: 0;
  margin-top: 18px;
}
.chart-head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 10px 28px;
  padding: 18px 22px 0;
}
.chart-head h3 {
  margin: 0;
  color: var(--tk-heading);
  font-size: 15px;
}
.chart-head .sub {
  color: var(--tk-muted);
  font-size: 12px;
}
.kpi {
  color: var(--tk-muted);
  font-size: 12px;
}
.kpi b {
  margin-right: 4px;
  color: var(--tk-heading);
  font-family: var(--tk-font-num);
  font-size: 24px;
}
.legend {
  display: flex;
  gap: 16px;
  margin-left: auto;
  color: var(--tk-muted);
  font-size: 12px;
}
.legend span {
  display: flex;
  align-items: center;
  gap: 6px;
}
.legend i {
  width: 10px;
  height: 10px;
  border-radius: 3px;
}
.recv {
  fill: var(--tk-primary);
  background: var(--tk-primary);
}
.sent {
  fill: var(--tk-orange);
  background: var(--tk-orange);
}
.idle {
  fill: var(--tk-line-strong);
}
.chart-wrap {
  position: relative;
  margin-top: 8px;
}
.chart {
  display: block;
  width: 100%;
  height: 150px;
}
.quiet {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: var(--tk-muted);
  font-size: 13px;
  pointer-events: none;
}
.axis {
  display: flex;
  justify-content: space-between;
  padding: 4px 22px 16px;
  color: var(--tk-muted);
  font-family: var(--tk-font-num);
  font-size: 11px;
}

/* ---------------- 底部三列 ---------------- */
.row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
  margin-top: 18px;
}
.note {
  margin: 12px 0 0;
  color: var(--tk-muted);
  font-size: 12px;
  line-height: 1.65;
}
.note.first {
  margin-top: 0;
}
.dot {
  width: 8px;
  height: 8px;
  flex: none;
  border-radius: 50%;
  background: var(--tk-off);
}
.dot.is-on {
  background: var(--tk-ok-solid);
}
.dot.is-pending {
  background: var(--tk-gold);
}
.dot.is-danger {
  background: var(--tk-coral);
}
.account-list,
.plugin-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.account-list li {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 11px 0;
  border-top: 1px solid var(--tk-line);
}
.account-list li:first-child {
  border-top: 0;
  padding-top: 2px;
}
.account-list li > div {
  min-width: 0;
  flex: 1;
}
.account-id {
  color: var(--tk-heading);
  font-family: var(--tk-font-num);
  font-size: 15px;
  font-weight: 700;
}
.meta {
  color: var(--tk-muted);
  font-size: 12px;
}
.plugin-big {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 8px;
}
.plugin-big b {
  color: var(--tk-heading);
  font-family: var(--tk-font-num);
  font-size: 34px;
  line-height: 1;
}
.plugin-big span {
  color: var(--tk-muted);
  font-size: 13px;
}
.seg-bar {
  display: flex;
  height: 8px;
  margin: 6px 0 12px;
  gap: 3px;
}
.seg-bar i {
  border-radius: 3px;
}
.plugin-list li {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
  font-size: 13px;
}
.plugin-name {
  min-width: 0;
  overflow-wrap: anywhere;
}
.plugin-state {
  margin-left: auto;
  color: var(--tk-muted);
  font-size: 12px;
  white-space: nowrap;
}
.all-ok {
  margin: 0;
  color: var(--tk-ok);
  font-size: 13px;
}
.res + .res {
  margin-top: 12px;
}
.res-head {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
}
.res-head span {
  color: var(--tk-muted);
  font-size: 12px;
}
.res-head b {
  color: var(--tk-heading);
  font-family: var(--tk-font-num);
  font-size: 14px;
}
.track {
  height: 6px;
  margin-top: 5px;
  overflow: hidden;
  border: 1px solid var(--tk-line);
  border-radius: 3px;
  background: var(--tk-surface-2);
}
.track i {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: var(--tk-primary);
}

@media (max-width: 1100px) {
  .row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 900px) {
  .hero {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 700px) {
  .row {
    grid-template-columns: minmax(0, 1fr);
  }
  .legend {
    margin-left: 0;
  }
}
@media (max-width: 520px) {
  .hero {
    padding: 22px 20px;
  }
  .hero h2 {
    font-size: 30px;
  }
  .hero-stats {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 18px 0;
  }
  .hero-stats > div,
  .hero-stats > div:first-child {
    padding: 0 0 0 18px;
    border-left: 1px solid rgba(255, 255, 255, 0.18);
  }
  .hero-stats > div:nth-child(odd) {
    padding-left: 0;
    border-left: 0;
  }
  .hero-stats b {
    font-size: 32px;
  }
}
</style>
