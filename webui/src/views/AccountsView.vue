<script setup lang="ts">
import { computed, ref } from 'vue'
import type { AccountAction, ManagedAccount } from '@/api'
import { api } from '@/api'
import PageContainer from '@/components/PageContainer.vue'
import PagePanel from '@/components/PagePanel.vue'
import ResourceBoundary from '@/components/ResourceBoundary.vue'
import { confirmAction } from '@/composables/confirm'
import { numberText } from '@/composables/format'
import { usePanelData } from '@/composables/usePanelData'
import AccountList from './accounts/AccountList.vue'
import AccountPrefsDialog from './accounts/AccountPrefsDialog.vue'
import PairingPanel from './accounts/PairingPanel.vue'
import StrategyPanel from './accounts/StrategyPanel.vue'
import { accountKey, accountName } from './accounts/helpers'

const { accounts, features, managedAccounts, refresh } = usePanelData()

const summaries = computed(() => accounts.data.value?.accounts ?? [])
const groupNames = computed(() => features.data.value?.group_names ?? {})

// 同一个群被多个账号配置时只计一次（取第一个），与总览口径一致。
const strategyTotals = computed(() => {
  const seen = new Map<string, string>()
  for (const account of summaries.value)
    for (const item of account.response_strategies)
      if (!seen.has(item.group_id)) seen.set(item.group_id, item.strategy)
  const values = [...seen.values()]
  return {
    random: values.filter((value) => value === 'random').length,
    deterministic: values.filter((value) => value === 'deterministic').length,
  }
})

const ACTIONS: Record<
  Exclude<AccountAction, 'preferences'>,
  { title: string; message: string; done: string }
> = {
  disable: {
    title: '停用账户',
    message: '停止响应并拒绝重新接入，重启后仍生效。',
    done: '账户已停用',
  },
  enable: { title: '启用账户', message: '允许协议端重新连接。', done: '账户已启用' },
  kick: {
    title: '踢下线',
    message: '只断开当前连接，协议端可能自动重连。',
    done: '已断开账户连接',
  },
  forget: {
    title: '移除记录',
    message: '移除账户的路由和偏好记录；不会删除协议端账户，之后仍可重新接入。',
    done: '账户记录已移除',
  },
}

const busy = ref('')
async function runAction(account: ManagedAccount, action: Exclude<AccountAction, 'preferences'>) {
  const text = ACTIONS[action]
  if (
    !(await confirmAction(
      `${text.message}账号 ${accountName(account)}（${account.id}）。`,
      text.title,
    ))
  )
    return
  busy.value = accountKey(account)
  try {
    await api.manage.accountAction(account.platform, account.id, action, { confirm: true })
    ElMessage.success(text.done)
    await refresh()
  } catch (error) {
    ElMessage.error(`${text.title}失败：${(error as Error).message}`)
  } finally {
    busy.value = ''
  }
}

const editing = ref<ManagedAccount | null>(null)
const prefsOpen = ref(false)
function editPreferences(account: ManagedAccount) {
  editing.value = account
  prefsOpen.value = true
}
</script>

<template>
  <PageContainer>
    <ResourceBoundary :resource="managedAccounts" label="账户数据">
      <template #default="{ data }">
        <div class="grid-4">
          <PagePanel class="kpi">
            <div class="num">{{ numberText(data.accounts.length) }}</div>
            <div class="lbl">账户总数</div>
          </PagePanel>
          <PagePanel class="kpi">
            <div class="num">
              {{ numberText(data.accounts.filter((item) => item.enabled && item.online).length) }}
              <small>/ {{ numberText(data.accounts.length) }}</small>
            </div>
            <div class="lbl">在线账户</div>
          </PagePanel>
          <PagePanel class="kpi">
            <div class="num">{{ numberText(strategyTotals.random) }}</div>
            <div class="lbl">随机响应群</div>
          </PagePanel>
          <PagePanel class="kpi">
            <div class="num">{{ numberText(strategyTotals.deterministic) }}</div>
            <div class="lbl">定向响应群</div>
          </PagePanel>
        </div>
      </template>
    </ResourceBoundary>

    <!-- 配对接入有自己的数据源，托管账户列表加载失败时也要能添加账户。 -->
    <PairingPanel />

    <template v-if="managedAccounts.data.value">
      <PagePanel title="托管账户" class="list-panel">
        <AccountList
          :accounts="managedAccounts.data.value.accounts"
          :summaries="summaries"
          :busy="busy"
          @preferences="editPreferences"
          @action="runAction"
        />
        <p class="note">
          停用会阻止响应和重新接入，重启后仍生效。“踢下线”只断开本次连接，协议端可能自动重连。
        </p>
      </PagePanel>
      <AccountPrefsDialog
        v-model="prefsOpen"
        :account="editing"
        :capabilities="managedAccounts.data.value.capabilities"
        @saved="refresh"
      />
    </template>

    <ResourceBoundary :resource="accounts" label="响应策略数据">
      <StrategyPanel :summaries="summaries" :group-names="groupNames" />
    </ResourceBoundary>
  </PageContainer>
</template>

<style scoped>
.grid-4 {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.kpi {
  --el-card-padding: 15px 16px;
}
.num {
  color: var(--tk-heading);
  font-family: var(--tk-font-num);
  font-size: 32px;
  font-variant-numeric: tabular-nums;
  font-weight: 700;
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
  letter-spacing: 0.06em;
}
.list-panel {
  margin-bottom: 16px;
}
.note {
  margin: 12px 0 0;
  color: var(--tk-muted);
  font-size: 12px;
  line-height: 1.65;
}

@media (max-width: 980px) {
  .grid-4 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 520px) {
  .grid-4 {
    gap: 10px;
  }
  .kpi {
    --el-card-padding: 12px;
  }
  .num {
    font-size: 26px;
  }
}
</style>
