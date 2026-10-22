<script setup lang="ts">
import { computed } from 'vue'
import type { AccountAction, GroupStatus, ManagedAccount } from '@/api'
import QqAvatar from '@/components/QqAvatar.vue'
import StatusTag from '@/components/StatusTag.vue'
import { numberText, platformText } from '@/composables/format'
import { useNarrow } from '@/composables/useNarrow'
import type { StatusKind } from '@/composables/status'
import AccountActions from './AccountActions.vue'
import { accountKey, accountName } from './helpers'

// 托管账户列表：桌面为 el-table，窄屏为卡片列表；操作通过事件交给页面处理。
const props = defineProps<{
  accounts: ManagedAccount[]
  groups: GroupStatus[]
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
    const own = props.groups.filter((group) =>
      group.accounts.some((item) => item.platform === account.platform && item.id === account.id),
    )
    const random = own.filter((group) => group.strategy === 'random').length
    // 定向只统计由该账号负责的群；同群的其他账号在定向群里只是候补。
    const leading = own.filter(
      (group) =>
        group.strategy === 'deterministic' &&
        group.accounts.some(
          (item) =>
            item.ref === group.deterministic_account &&
            item.platform === account.platform &&
            item.id === account.id,
        ),
    ).length
    const chips = [random && `随机 ${random} 群`, leading && `定向负责 ${leading} 群`].filter(
      (item): item is string => !!item,
    )
    // 没有标签时：有群说明这些群都定向给了别的账号，本账号只是候补。
    const idle = own.length ? '定向候补' : '未绑定群'
    return { account, key: accountKey(account), status: status(account), chips, idle }
  }),
)
</script>

<template>
  <el-table v-if="!narrow" :data="rows" row-key="key" class="account-table">
    <template #empty>尚无账户。展开上方“配对接入”连接第一个账户。</template>
    <el-table-column label="账号" min-width="150">
      <template #default="{ row }">
        <div class="who">
          <QqAvatar
            :id="row.account.id"
            :name="accountName(row.account)"
            :platform="row.account.platform"
            kind="user"
          />
          <div>
            <strong class="name">{{ accountName(row.account) }}</strong>
            <span class="tk-mono sub">{{ row.account.id }}</span>
          </div>
        </div>
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
        <StatusTag v-else kind="off">{{ row.idle }}</StatusTag>
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
        <div class="title who">
          <QqAvatar
            :id="row.account.id"
            :name="accountName(row.account)"
            :platform="row.account.platform"
            kind="user"
          />
          <div>
            <strong class="name">{{ accountName(row.account) }}</strong>
            <span class="tk-mono sub">{{ row.account.id }}</span>
          </div>
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
            <StatusTag v-else kind="off">{{ row.idle }}</StatusTag>
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
  letter-spacing: 0.04em;
}
.who {
  display: flex;
  align-items: center;
  gap: 10px;
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
  border-radius: 10px;
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
  border-top: 1px solid var(--tk-line);
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
