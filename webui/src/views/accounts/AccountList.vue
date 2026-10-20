<script setup lang="ts">
import { computed } from 'vue'
import type { AccountAction, AccountSummary, ManagedAccount } from '@/api'
import StatusTag from '@/components/StatusTag.vue'
import { numberText, platformText } from '@/composables/format'
import { useNarrow } from '@/composables/useNarrow'
import type { StatusKind } from '@/composables/status'
import AccountActions from './AccountActions.vue'
import { accountKey, accountName, strategyCounts } from './helpers'

// 托管账户列表：桌面为 el-table，窄屏为卡片列表；操作通过事件交给页面处理。
const props = defineProps<{
  accounts: ManagedAccount[]
  summaries: AccountSummary[]
  /** 正在执行操作的账号（platform/id），其按钮暂时禁用。 */
  busy: string
}>()
const emit = defineEmits<{
  preferences: [account: ManagedAccount]
  action: [account: ManagedAccount, action: Exclude<AccountAction, 'preferences'>]
}>()

const narrow = useNarrow()

function status(account: ManagedAccount): { kind: StatusKind; label: string } {
  if (!account.enabled) return { kind: 'off', label: '已停用' }
  return account.online ? { kind: 'on', label: '在线' } : { kind: 'pending', label: '离线' }
}

const rows = computed(() =>
  props.accounts.map((account) => {
    const summary = props.summaries.find(
      (item) => item.id === account.id && item.platform === account.platform,
    )
    const counts = strategyCounts(summary)
    const chips = [
      counts.random && `随机 ${counts.random} 群`,
      counts.deterministic && `定向 ${counts.deterministic} 群`,
      counts.other && `其他 ${counts.other} 群`,
    ].filter((item): item is string => !!item)
    return { account, key: accountKey(account), status: status(account), chips }
  }),
)
</script>

<template>
  <el-table v-if="!narrow" :data="rows" row-key="key" class="account-table">
    <template #empty>尚无账户。展开上方“配对接入”连接第一个账户。</template>
    <el-table-column label="账号" min-width="150">
      <template #default="{ row }">
        <strong class="name">{{ accountName(row.account) }}</strong>
        <span class="tk-mono sub">{{ row.account.id }}</span>
      </template>
    </el-table-column>
    <el-table-column label="平台" min-width="96">
      <template #default="{ row }">{{ platformText(row.account.platform) }}</template>
    </el-table-column>
    <el-table-column label="状态" min-width="84">
      <template #default="{ row }">
        <StatusTag :kind="row.status.kind">{{ row.status.label }}</StatusTag>
      </template>
    </el-table-column>
    <el-table-column label="绑定群" min-width="72">
      <template #default="{ row }">{{ numberText(row.account.group_count) }}</template>
    </el-table-column>
    <el-table-column label="响应策略" min-width="150">
      <template #default="{ row }">
        <div v-if="row.chips.length" class="chips">
          <el-tag
            v-for="chip in row.chips"
            :key="chip"
            size="small"
            effect="plain"
            disable-transitions
          >
            {{ chip }}
          </el-tag>
        </div>
        <StatusTag v-else kind="pending">未设置</StatusTag>
      </template>
    </el-table-column>
    <el-table-column label="操作" min-width="290">
      <template #default="{ row }">
        <AccountActions
          :account="row.account"
          :busy="busy === row.key"
          @preferences="emit('preferences', row.account)"
          @action="(name) => emit('action', row.account, name)"
        />
      </template>
    </el-table-column>
  </el-table>

  <div v-else class="cards">
    <el-empty
      v-if="!rows.length"
      :image-size="48"
      description="尚无账户。展开上方“配对接入”连接第一个账户。"
    />
    <article v-for="row in rows" :key="row.key" class="card">
      <header>
        <div class="title">
          <strong class="name">{{ accountName(row.account) }}</strong>
          <span class="tk-mono sub">{{ row.account.id }}</span>
        </div>
        <StatusTag :kind="row.status.kind">{{ row.status.label }}</StatusTag>
      </header>
      <dl>
        <div>
          <dt>平台</dt>
          <dd>{{ platformText(row.account.platform) }}</dd>
        </div>
        <div>
          <dt>绑定群</dt>
          <dd>{{ numberText(row.account.group_count) }}</dd>
        </div>
        <div>
          <dt>响应策略</dt>
          <dd>
            <span v-if="row.chips.length" class="chips">
              <el-tag
                v-for="chip in row.chips"
                :key="chip"
                size="small"
                effect="plain"
                disable-transitions
              >
                {{ chip }}
              </el-tag>
            </span>
            <StatusTag v-else kind="pending">未设置</StatusTag>
          </dd>
        </div>
      </dl>
      <AccountActions
        :account="row.account"
        :busy="busy === row.key"
        @preferences="emit('preferences', row.account)"
        @action="(name) => emit('action', row.account, name)"
      />
    </article>
  </div>
</template>

<style scoped>
.account-table {
  --el-table-header-bg-color: transparent;
  --el-table-header-text-color: var(--tk-muted);
  --el-table-tr-bg-color: transparent;
  --el-table-row-hover-bg-color: var(--tk-surface-2);
  width: 100%;
}
.account-table :deep(th.el-table__cell) {
  font-size: 12px;
  letter-spacing: 1.5px;
}
.name {
  display: block;
  color: var(--tk-heading);
  overflow-wrap: anywhere;
}
.sub {
  display: block;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.cards {
  display: grid;
  gap: 10px;
}
.card {
  padding: 12px 14px;
  border: 1px solid var(--tk-line);
  border-radius: 4px;
  background: var(--tk-surface-2);
}
.card header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
.card .title {
  min-width: 0;
}
.card dl {
  margin: 10px 0;
}
.card dl > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 5px 0;
  border-top: 1px dashed var(--tk-line-strong);
}
.card dt {
  flex: none;
  color: var(--tk-muted);
  font-size: 12px;
}
.card dd {
  min-width: 0;
  margin: 0;
  text-align: right;
}
.card .chips {
  justify-content: flex-end;
}
</style>
