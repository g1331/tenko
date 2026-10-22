<script setup lang="ts">
import { computed, ref } from 'vue'
import { WarningFilled } from '@element-plus/icons-vue'
import { api, ApiError, type GroupAccount, type GroupStatus } from '@/api'
import QqAvatar from '@/components/QqAvatar.vue'
import PagePanel from '@/components/PagePanel.vue'
import { numberText, platformText } from '@/composables/format'
import { usePanelData } from '@/composables/usePanelData'
import { memberName, memberStateText, ROLE_LABELS, silentText } from './helpers'

// 群响应策略：一行一个群，列出绑定账号在本群的状态，标出不会响应的群，并可直接修改策略。
const props = defineProps<{ groups: GroupStatus[]; groupNames: Record<string, string> }>()

const { refresh } = usePanelData()
const query = ref('')
const strategy = ref('')
const pending = ref(new Set<string>())

const strategyOptions = [
  { label: '随机', value: 'random' },
  { label: '定向', value: 'deterministic' },
]

// 不会响应的群排在最前，其余按群号排序。
const rows = computed(() =>
  props.groups
    .map((group) => ({ ...group, name: props.groupNames[group.group_id] ?? '' }))
    .sort(
      (a, b) =>
        Number(!a.silent_reason) - Number(!b.silent_reason) ||
        a.group_id.length - b.group_id.length ||
        a.group_id.localeCompare(b.group_id),
    ),
)
type Row = (typeof rows.value)[number]

const silentCount = computed(() => rows.value.filter((row) => row.silent_reason).length)

const filtered = computed(() => {
  const text = query.value.trim().toLowerCase()
  return rows.value.filter(
    (row) =>
      (!strategy.value ||
        (strategy.value === 'silent' ? !!row.silent_reason : row.strategy === strategy.value)) &&
      (!text ||
        [row.group_id, row.name, ...row.accounts.flatMap((a) => [a.id, a.name ?? ''])]
          .join(' ')
          .toLowerCase()
          .includes(text)),
  )
})

const label = (row: Row) => row.name || row.group_id
const isTarget = (row: Row, account: GroupAccount) =>
  row.strategy === 'deterministic' && row.deterministic_account === account.ref

async function save(row: Row, next: 'random' | 'deterministic', account: string | null) {
  if (pending.value.has(row.group_id)) return
  pending.value = new Set(pending.value).add(row.group_id)
  try {
    await api.manage.setGroupResponse(row.group_id, { strategy: next, account })
    await refresh()
    ElMessage.success(`${label(row)} 已设为${next === 'random' ? '随机' : '定向'}响应`)
  } catch (reason) {
    ElMessage.error(`修改失败：${reason instanceof ApiError ? reason.message : String(reason)}`)
  } finally {
    const rest = new Set(pending.value)
    rest.delete(row.group_id)
    pending.value = rest
  }
}

// 切回定向时沿用该群已指定的账号，只有单账号群才不传账号。
const setStrategy = (row: Row, value: string | number | boolean) =>
  save(
    row,
    value as 'random' | 'deterministic',
    value === 'deterministic' && row.accounts.length > 1 ? row.deterministic_account : null,
  )

const optionLabel = (account: GroupAccount) =>
  [memberName(account), memberStateText(account)].filter(Boolean).join(' · ')
</script>

<template>
  <PagePanel title="群响应策略">
    <template #extra>
      共 {{ numberText(rows.length) }} 个群
      <button
        v-if="silentCount"
        type="button"
        class="silent-count"
        @click="strategy = strategy === 'silent' ? '' : 'silent'"
      >
        · {{ numberText(silentCount) }} 个群不会响应
      </button>
    </template>
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
          <el-option label="不会响应" value="silent" />
        </el-select>
      </div>
      <el-table :data="filtered" row-key="group_id" class="strategy-table">
        <template #empty>没有匹配的群</template>
        <el-table-column label="群" min-width="200">
          <template #default="{ row }">
            <div class="group">
              <QqAvatar :id="row.group_id" :name="row.name" />
              <div class="group-text">
                <strong class="name">{{ row.name || row.group_id }}</strong>
                <span v-if="row.name" class="tk-mono sub">{{ row.group_id }}</span>
                <span v-if="row.silent_reason" class="silent">
                  <el-icon><WarningFilled /></el-icon>{{ silentText(row as Row) }}
                </span>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="绑定账号" min-width="220">
          <template #default="{ row }">
            <ul class="members">
              <li
                v-for="account in row.accounts as GroupAccount[]"
                :key="account.ref"
                :class="['member', `is-${account.state}`]"
                :title="platformText(account.platform)"
              >
                <i class="dot" aria-hidden="true" />
                <QqAvatar
                  :id="account.id"
                  :name="memberName(account)"
                  :platform="account.platform"
                  :size="20"
                  kind="user"
                />
                <span class="member-name">{{ memberName(account) }}</span>
                <span v-if="account.name" class="tk-mono member-id">{{ account.id }}</span>
                <span v-if="isTarget(row as Row, account)" class="badge is-target">定向</span>
                <span v-if="account.role && ROLE_LABELS[account.role]" class="badge">{{
                  ROLE_LABELS[account.role]
                }}</span>
                <span v-if="memberStateText(account)" class="state">{{
                  memberStateText(account)
                }}</span>
              </li>
            </ul>
          </template>
        </el-table-column>
        <el-table-column label="策略" min-width="250">
          <template #default="{ row }">
            <div class="control">
              <el-segmented
                :model-value="row.strategy"
                :options="strategyOptions"
                size="small"
                :disabled="pending.has(row.group_id)"
                :aria-label="`${label(row as Row)} 响应策略`"
                @change="(value: string | number | boolean) => setStrategy(row as Row, value)"
              />
              <el-select
                v-if="row.strategy === 'deterministic' && row.accounts.length > 1"
                :model-value="row.deterministic_account"
                size="small"
                placeholder="选择账号"
                :disabled="pending.has(row.group_id)"
                :aria-label="`${label(row as Row)} 定向账号`"
                @change="(value: string) => save(row as Row, 'deterministic', value)"
              >
                <el-option
                  v-for="account in row.accounts as GroupAccount[]"
                  :key="account.ref"
                  :label="optionLabel(account)"
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
.silent-count {
  padding: 0;
  border: 0;
  background: none;
  color: var(--el-color-danger);
  cursor: pointer;
  font: inherit;
  font-weight: 600;
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
.group-text {
  min-width: 0;
}
.name {
  display: block;
  color: var(--tk-heading);
  overflow-wrap: anywhere;
}
.sub {
  display: block;
}
.silent {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  margin-top: 3px;
  color: var(--el-color-danger);
  font-size: 12px;
  line-height: 1.5;
}
.silent .el-icon {
  flex: none;
  margin-top: 2px;
}
.members {
  display: grid;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.member {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px 6px;
  font-size: 13px;
}
.dot {
  width: 7px;
  height: 7px;
  flex: none;
  border-radius: 50%;
  background: var(--tk-ok-solid);
}
.member.is-muted .dot {
  background: var(--el-color-warning);
}
.member.is-offline .dot,
.member.is-disabled .dot {
  background: var(--tk-muted);
}
.member.is-offline .member-name,
.member.is-disabled .member-name {
  color: var(--tk-muted);
}
.member-name {
  color: var(--tk-heading);
  font-weight: 600;
}
.member-id {
  font-size: 11px;
}
.badge {
  padding: 0 5px;
  border: 1px solid var(--tk-line);
  border-radius: 4px;
  color: var(--tk-muted);
  font-size: 11px;
  line-height: 16px;
}
.badge.is-target {
  border-color: var(--tk-primary);
  color: var(--tk-primary);
}
.state {
  color: var(--tk-muted);
  font-size: 12px;
}
.member.is-muted .state {
  color: var(--el-color-warning);
}
.control {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.control .el-select {
  flex: 0 0 180px;
}
</style>
