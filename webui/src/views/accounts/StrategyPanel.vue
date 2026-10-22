<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, ApiError, type AccountSummary } from '@/api'
import GroupAvatar from '@/components/GroupAvatar.vue'
import PagePanel from '@/components/PagePanel.vue'
import { numberText, platformText } from '@/composables/format'
import { usePanelData } from '@/composables/usePanelData'

// 群响应策略：一行一个群，可按群名 / 群号 / 账号搜索、按策略筛选，并直接修改策略与定向账号。
const props = defineProps<{ summaries: AccountSummary[]; groupNames: Record<string, string> }>()

const { refresh } = usePanelData()
const query = ref('')
const strategy = ref('')
const pending = ref(new Set<string>())

const strategyOptions = [
  { label: '随机', value: 'random' },
  { label: '定向', value: 'deterministic' },
]

const rows = computed(() => {
  const groups = new Map<
    string,
    {
      groupId: string
      groupName: string
      strategy: string
      deterministicAccount: string | null
      accounts: { ref: string; id: string; platform: string }[]
    }
  >()
  for (const account of props.summaries)
    for (const item of account.response_strategies) {
      const row = groups.get(item.group_id) ?? {
        groupId: item.group_id,
        groupName: props.groupNames[item.group_id] ?? '',
        strategy: item.strategy,
        deterministicAccount: item.deterministic_account,
        accounts: [],
      }
      row.accounts.push({ ref: account.ref, id: account.id, platform: account.platform })
      groups.set(item.group_id, row)
    }
  return [...groups.values()].sort(
    (a, b) => a.groupId.length - b.groupId.length || a.groupId.localeCompare(b.groupId),
  )
})
type Row = (typeof rows.value)[number]

const filtered = computed(() => {
  const text = query.value.trim().toLowerCase()
  return rows.value.filter(
    (row) =>
      (!strategy.value || row.strategy === strategy.value) &&
      (!text ||
        `${row.groupId} ${row.groupName} ${row.accounts.map((a) => a.id).join(' ')}`
          .toLowerCase()
          .includes(text)),
  )
})

const label = (row: Row) => row.groupName || row.groupId

async function save(row: Row, next: 'random' | 'deterministic', account: string | null) {
  if (pending.value.has(row.groupId)) return
  pending.value = new Set(pending.value).add(row.groupId)
  try {
    await api.manage.setGroupResponse(row.groupId, { strategy: next, account })
    await refresh()
    ElMessage.success(`${label(row as Row)} 已设为${next === 'random' ? '随机' : '定向'}响应`)
  } catch (reason) {
    ElMessage.error(`修改失败：${reason instanceof ApiError ? reason.message : String(reason)}`)
  } finally {
    const rest = new Set(pending.value)
    rest.delete(row.groupId)
    pending.value = rest
  }
}

// 切回定向时沿用该群已指定的账号，只有单账号群才不传账号。
const setStrategy = (row: Row, value: string | number | boolean) =>
  save(
    row,
    value as 'random' | 'deterministic',
    value === 'deterministic' && row.accounts.length > 1 ? row.deterministicAccount : null,
  )
</script>

<template>
  <PagePanel title="群响应策略">
    <template #extra>共 {{ numberText(rows.length) }} 个群</template>
    <el-empty v-if="!rows.length" :image-size="48" description="尚未为任何群设置响应策略" />
    <template v-else>
      <div class="filters">
        <el-input
          v-model="query"
          clearable
          placeholder="搜索群名 / 群号 / 账号"
          aria-label="搜索群响应策略"
        />
        <el-select v-model="strategy" clearable placeholder="全部策略" aria-label="按策略筛选">
          <el-option label="随机" value="random" />
          <el-option label="定向" value="deterministic" />
        </el-select>
      </div>
      <el-table :data="filtered" row-key="groupId" class="strategy-table">
        <template #empty>没有匹配的群</template>
        <el-table-column label="群" min-width="150">
          <template #default="{ row }">
            <div class="group">
              <GroupAvatar :id="row.groupId" :name="row.groupName" />
              <div>
                <strong class="name">{{ row.groupName || row.groupId }}</strong>
                <span v-if="row.groupName" class="tk-mono sub">{{ row.groupId }}</span>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="绑定账号" min-width="110">
          <template #default="{ row }">
            <span
              v-for="account in row.accounts"
              :key="account.ref"
              class="tk-mono account"
              :title="platformText(account.platform)"
              >{{ account.id }}</span
            >
          </template>
        </el-table-column>
        <el-table-column label="策略" min-width="250">
          <template #default="{ row }">
            <div class="control">
              <el-segmented
                :model-value="row.strategy"
                :options="strategyOptions"
                size="small"
                :disabled="pending.has(row.groupId)"
                :aria-label="`${label(row as Row)} 响应策略`"
                @change="(value: string | number | boolean) => setStrategy(row as Row, value)"
              />
              <el-select
                v-if="row.strategy === 'deterministic' && row.accounts.length > 1"
                :model-value="row.deterministicAccount"
                size="small"
                placeholder="选择账号"
                :disabled="pending.has(row.groupId)"
                :aria-label="`${label(row as Row)} 定向账号`"
                @change="(value: string) => save(row as Row, 'deterministic', value)"
              >
                <el-option
                  v-for="account in row.accounts"
                  :key="account.ref"
                  :label="account.id"
                  :value="account.ref"
                />
              </el-select>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </template>
  </PagePanel>
</template>

<style scoped>
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}
.filters .el-input {
  flex: 1 1 220px;
  max-width: 320px;
}
.filters .el-select {
  flex: 0 0 140px;
}
.strategy-table {
  --el-table-header-bg-color: transparent;
  --el-table-header-text-color: var(--tk-muted);
  --el-table-tr-bg-color: transparent;
  --el-table-row-hover-bg-color: var(--tk-surface-2);
  width: 100%;
}
.strategy-table :deep(th.el-table__cell) {
  font-size: 12px;
  letter-spacing: 0.04em;
}
.group {
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
.account {
  display: inline-block;
  margin-right: 10px;
}
.control {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.control .el-select {
  flex: 0 0 140px;
}
</style>
