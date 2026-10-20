<script setup lang="ts">
import { computed } from 'vue'
import PageContainer from '@/components/PageContainer.vue'
import PagePanel from '@/components/PagePanel.vue'
import ResourceBoundary from '@/components/ResourceBoundary.vue'
import StatusTag from '@/components/StatusTag.vue'
import { durationText, isNumber, numberText, platformText } from '@/composables/format'
import { accountStatus, pluginStats, type StatusKind } from '@/composables/status'
import { usePanelData } from '@/composables/usePanelData'

const { overview, accounts, features } = usePanelData()

const accountList = computed(() => accounts.data.value?.accounts ?? null)
const featureList = computed(() => features.data.value?.features ?? null)
const stats = computed(() => (featureList.value ? pluginStats(featureList.value) : null))
const offlineCount = computed(() => accountList.value?.filter((item) => !item.online).length ?? 0)

const accountDelta = computed(() => {
  if (!accountList.value) return ''
  if (!accountList.value.length) return '暂无账户'
  return offlineCount.value ? `${offlineCount.value} 个离线` : '全部在线'
})

const platforms = computed(() => [
  ...new Set((accountList.value ?? []).map((item) => platformText(item.platform))),
])

const received = computed(() => overview.data.value?.messages.received_count)
const sent = computed(() => overview.data.value?.messages.sent_count)
const totalMessages = computed(() =>
  isNumber(received.value) && isNumber(sent.value) ? received.value + sent.value : null,
)
const sentShare = computed(() => {
  const total = (received.value ?? 0) + (sent.value ?? 0)
  return total ? ((sent.value ?? 0) / total) * 100 : 0
})
const percentOf = (value: number | undefined) => {
  const total = (received.value ?? 0) + (sent.value ?? 0)
  return total ? ((value ?? 0) / total) * 100 : 0
}

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

interface AttentionItem {
  kind: StatusKind
  tag: string
  text: string
  to?: string
}
const attention = computed<AttentionItem[]>(() => {
  const items: AttentionItem[] = []
  for (const account of accountList.value ?? []) {
    if (!account.online)
      items.push({ kind: 'danger', tag: '异常', text: `账号 ${account.id} 离线`, to: '/accounts' })
  }
  for (const feature of featureList.value ?? []) {
    const name = feature.name || feature.plugin
    if (!feature.loaded)
      items.push({ kind: 'pending', tag: '提示', text: `${name} 未加载`, to: '/plugins' })
    else if (feature.maintenance)
      items.push({ kind: 'pending', tag: '提示', text: `${name} 处于维护状态`, to: '/plugins' })
    else if (!feature.enabled)
      items.push({ kind: 'off', tag: '停用', text: `${name} 已停用`, to: '/plugins' })
  }
  const failed: Array<[string, { error: { value: unknown } }]> = [
    ['总览', overview],
    ['账户', accounts],
    ['插件', features],
  ]
  for (const [label, source] of failed) {
    if (source.error.value)
      items.push({ kind: 'danger', tag: '异常', text: `${label}数据读取失败` })
  }
  return items
})
</script>

<template>
  <PageContainer>
    <ResourceBoundary :resource="overview" label="运行数据">
      <template #default="{ data }">
        <section class="hero" aria-label="Tenko 概览">
          <div class="hero-title" aria-hidden="true">TENKO</div>
          <div class="hero-copy">
            <div class="hero-eyebrow">TENKO · GROUP CONTROL PANEL</div>
            <div class="ribbon">群组管理 · 控制台</div>
          </div>
          <div class="hero-tags">
            <el-tag v-if="data.version" effect="dark" class="tag-navy" disable-transitions>
              v{{ data.version }}
            </el-tag>
            <el-tag type="danger" effect="dark" disable-transitions>
              {{ numberText(data.online_accounts) }} ONLINE
            </el-tag>
            <el-tag v-for="item in platforms" :key="item" effect="plain" disable-transitions>
              {{ item }}
            </el-tag>
          </div>
        </section>

        <div class="grid-4">
          <PagePanel class="kpi">
            <div class="num">{{ durationText(data.uptime_seconds) }}</div>
            <div class="lbl">本次运行时长</div>
            <div class="delta">{{ data.version ? `版本 v${data.version}` : '' }}&nbsp;</div>
          </PagePanel>
          <PagePanel class="kpi">
            <div class="num">
              {{ numberText(data.online_accounts) }}
              <small v-if="accountList">/ {{ numberText(accountList.length) }}</small>
            </div>
            <div class="lbl">在线账号</div>
            <div class="delta" :class="{ up: accountList?.length && !offlineCount }">
              {{ accountDelta }}&nbsp;
            </div>
          </PagePanel>
          <PagePanel class="kpi">
            <div class="num">{{ numberText(data.active_groups) }}</div>
            <div class="lbl">活动群组</div>
            <div class="delta">{{ stats ? `${stats.loaded} 个插件已加载` : '' }}&nbsp;</div>
          </PagePanel>
          <PagePanel class="kpi" accent="gold">
            <div class="num">{{ numberText(totalMessages) }}</div>
            <div class="lbl">收发消息合计</div>
            <div class="delta">收 {{ numberText(received) }} · 发 {{ numberText(sent) }}</div>
          </PagePanel>
        </div>
      </template>
    </ResourceBoundary>

    <div class="grid-2">
      <PagePanel title="账号状态">
        <template v-if="accountList" #extra>
          <router-link class="tk-link" to="/accounts"
            >管理全部 {{ numberText(accountList.length) }} 个账户</router-link
          >
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
                <span class="status-dot" :class="{ on: account.online }" aria-hidden="true" />
                <div>
                  <span class="tk-mono strong">{{ account.id }}</span>
                  <div class="meta">
                    {{ platformText(account.platform) }} · 绑定
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

      <PagePanel title="插件概况" accent="coral">
        <template #extra
          ><router-link class="tk-link" to="/plugins">查看详情</router-link></template
        >
        <ResourceBoundary :resource="features" label="插件数据">
          <template #default>
            <template v-if="stats">
              <div class="stat-trio">
                <div class="stat">
                  <b
                    >{{ stats.loaded }}<small> / {{ stats.total }}</small></b
                  >
                  <span>已加载</span>
                </div>
                <div class="stat">
                  <b>{{ stats.maintenance }}</b
                  ><span>维护中</span>
                </div>
                <div class="stat">
                  <b>{{ stats.disabled }}</b
                  ><span>已停用</span>
                </div>
              </div>
              <div
                class="stat-bar"
                role="img"
                :aria-label="segments.map((item) => `${item.label} ${item.count}`).join('，')"
              >
                <i
                  v-for="item in segments"
                  :key="item.key"
                  :style="{
                    width: `${stats.total ? (item.count / stats.total) * 100 : 0}%`,
                    background: item.color,
                  }"
                />
              </div>
              <div class="bar-legend">
                <span v-for="item in segments" :key="item.key">
                  <i :style="{ background: item.color }" />{{ item.label }} {{ item.count }}
                </span>
              </div>
              <p class="note">{{ stats.protectedCount }} 个核心插件受保护，不可停用。</p>
            </template>
          </template>
        </ResourceBoundary>
      </PagePanel>
    </div>

    <div class="grid-2">
      <PagePanel title="待关注">
        <div v-if="!overview.data.value && !accountList && !featureList" class="note">
          数据读取完成后显示。
        </div>
        <template v-else>
          <ul v-if="attention.length" class="attention-list">
            <li v-for="(item, index) in attention.slice(0, 6)" :key="index">
              <StatusTag :kind="item.kind">{{ item.tag }}</StatusTag>
              <span>{{ item.text }}</span>
              <router-link v-if="item.to" class="tk-link" :to="item.to">处理</router-link>
            </li>
          </ul>
          <div v-else class="attention-ok">
            <span aria-hidden="true">✓</span> 账号全部在线，插件运行正常。
          </div>
          <p v-if="attention.length > 6" class="note">另有 {{ attention.length - 6 }} 项未列出。</p>
        </template>
      </PagePanel>

      <PagePanel title="消息收发" accent="gold">
        <template #extra>自本次启动</template>
        <ResourceBoundary :resource="overview" label="消息数据">
          <template #default>
            <div class="flow-row">
              <div class="flow-head">
                <span>接收</span><b>{{ numberText(received) }}</b>
              </div>
              <el-progress
                :percentage="percentOf(received)"
                :show-text="false"
                :stroke-width="8"
                color="var(--tk-ok-solid)"
              />
            </div>
            <div class="flow-row">
              <div class="flow-head">
                <span>发送</span><b>{{ numberText(sent) }}</b>
              </div>
              <el-progress
                :percentage="percentOf(sent)"
                :show-text="false"
                :stroke-width="8"
                color="var(--tk-coral)"
              />
            </div>
            <p class="note">
              {{ totalMessages ? `发送占比 ${sentShare.toFixed(1)}%。` : '本次启动后暂无消息。' }}
            </p>
          </template>
        </ResourceBoundary>
      </PagePanel>
    </div>
  </PageContainer>
</template>

<style scoped>
.hero {
  position: relative;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 28px;
  margin-bottom: 16px;
  padding: 18px 26px;
  overflow: hidden;
  border-radius: 6px;
  background:
    repeating-linear-gradient(0deg, transparent 0 31px, rgba(45, 74, 107, 0.05) 31px 32px),
    repeating-linear-gradient(90deg, transparent 0 31px, rgba(45, 74, 107, 0.05) 31px 32px),
    var(--tk-surface);
  box-shadow: var(--tk-shadow);
}
.hero::before {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 4px;
  background: var(--tk-coral);
  content: '';
}
.hero-title {
  color: var(--tk-heading);
  font-family: 'Arial Black', var(--tk-font);
  font-size: 2.4rem;
  font-weight: 900;
  letter-spacing: 8px;
  line-height: 1;
}
.hero-copy {
  min-width: 0;
  flex: 1 1 220px;
}
.hero-eyebrow {
  color: var(--tk-kicker);
  font-size: 10px;
  font-style: italic;
  font-weight: 700;
  letter-spacing: 3px;
}
.ribbon {
  display: inline-block;
  margin-top: 6px;
  padding: 5px 20px 5px 13px;
  background: var(--tk-badge-navy);
  clip-path: polygon(0 0, 100% 0, calc(100% - 12px) 100%, 0 100%);
  color: #f5f1e8;
  font-size: 11px;
  letter-spacing: 3px;
}
.hero-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}
.hero-tags .el-tag {
  font-weight: 600;
  letter-spacing: 1.2px;
}
.tag-navy {
  --el-tag-bg-color: var(--tk-badge-navy);
  --el-tag-border-color: var(--tk-badge-navy);
  --el-tag-text-color: #f5f1e8;
}
html.dark .tag-navy {
  --el-tag-text-color: #f5f1e8;
}

.grid-4 {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
.grid-2 {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr);
  gap: 16px;
  margin-top: 16px;
}
.kpi {
  --el-card-padding: 15px 16px;
  min-height: 104px;
}
.num {
  color: var(--tk-heading);
  font-size: 27px;
  font-variant-numeric: tabular-nums;
  font-weight: 800;
  line-height: 1.15;
}
.num small {
  color: var(--tk-muted);
  font-size: 14px;
  font-weight: 600;
}
.lbl {
  margin-top: 6px;
  color: var(--tk-muted);
  font-size: 12px;
  letter-spacing: 1.5px;
}
.delta {
  margin-top: 3px;
  color: var(--tk-muted);
  font-size: 12px;
}
.delta.up {
  color: var(--tk-ok);
}
.note {
  margin: 12px 0 0;
  color: var(--tk-muted);
  font-size: 12px;
  line-height: 1.65;
}

.account-list,
.attention-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.account-list li {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 4px 12px;
  padding: 10px 0;
  border-bottom: 1px dashed var(--tk-line-strong);
}
.account-list li:last-child {
  border-bottom: 0;
}
.status-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--tk-off);
}
.status-dot.on {
  background: var(--tk-ok-solid);
  box-shadow: 0 0 0 3px rgba(46, 139, 87, 0.2);
}
.strong {
  font-weight: 700;
}
.meta {
  color: var(--tk-muted);
  font-size: 12px;
}

.stat-trio {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}
.stat {
  padding: 10px 12px;
  border: 1px solid var(--tk-line);
  border-radius: 4px;
  background: var(--tk-surface-2);
}
.stat b {
  display: block;
  color: var(--tk-heading);
  font-size: 22px;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}
.stat b small {
  color: var(--tk-muted);
  font-size: 14px;
  font-weight: 600;
}
.stat span {
  color: var(--tk-muted);
  font-size: 12px;
  letter-spacing: 1px;
}
.stat-bar {
  display: flex;
  height: 10px;
  margin-top: 14px;
  overflow: hidden;
  border-radius: 5px;
  background: var(--tk-off-bg);
}
.stat-bar i {
  display: block;
  height: 100%;
}
.bar-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin-top: 8px;
  color: var(--tk-muted);
  font-size: 12px;
}
.bar-legend i {
  display: inline-block;
  width: 9px;
  height: 9px;
  margin-right: 5px;
  border-radius: 2px;
}

.flow-row + .flow-row {
  margin-top: 12px;
}
.flow-head {
  display: flex;
  justify-content: space-between;
  color: var(--tk-text);
  font-size: 13px;
}
.flow-head b {
  color: var(--tk-heading);
  font-variant-numeric: tabular-nums;
}

.attention-list li {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 0;
  border-bottom: 1px dashed var(--tk-line-strong);
}
.attention-list li:last-child {
  border-bottom: 0;
}
.attention-list .tk-link {
  margin-left: auto;
  white-space: nowrap;
}
.attention-ok {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--tk-ok);
}

@media (max-width: 980px) {
  .grid-4 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .grid-2 {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 820px) {
  .hero-title {
    font-size: 2rem;
    letter-spacing: 6px;
  }
}
@media (max-width: 520px) {
  .grid-4 {
    gap: 10px;
  }
  .kpi {
    --el-card-padding: 12px;
    min-height: 92px;
  }
  .num {
    font-size: 22px;
  }
  .hero {
    padding: 14px 16px 14px 20px;
  }
  .hero-title {
    font-size: 1.7rem;
    letter-spacing: 5px;
  }
  .stat-trio {
    gap: 6px;
  }
  .stat b {
    font-size: 19px;
  }
}
</style>
