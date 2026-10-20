<script setup lang="ts">
import { computed, ref } from 'vue'
import type { AccountSummary } from '@/api'
import PagePanel from '@/components/PagePanel.vue'
import { numberText, platformText } from '@/composables/format'
import { strategyLabel } from './helpers'

// 每个群在各账号下的响应策略：可按群名 / 群号 / 账号搜索，并按策略筛选。
const props = defineProps<{ summaries: AccountSummary[]; groupNames: Record<string, string> }>()

const query = ref('')
const strategy = ref('')

const rows = computed(() =>
  props.summaries
    .flatMap((account) =>
      account.response_strategies.map((item) => ({
        key: `${account.platform}/${account.id}/${item.group_id}`,
        groupId: item.group_id,
        groupName: props.groupNames[item.group_id] ?? '',
        account: account.id,
        platform: account.platform,
        strategy: item.strategy,
      })),
    )
    .sort(
      (a, b) =>
        a.groupId.length - b.groupId.length ||
        a.groupId.localeCompare(b.groupId) ||
        a.account.localeCompare(b.account),
    ),
)

const filtered = computed(() => {
  const text = query.value.trim().toLowerCase()
  return rows.value.filter(
    (row) =>
      (!strategy.value || row.strategy === strategy.value) &&
      (!text || `${row.groupId} ${row.groupName} ${row.account}`.toLowerCase().includes(text)),
  )
})
</script>

<template>
  <PagePanel title="群响应策略" accent="navy">
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
      <el-table :data="filtered" row-key="key" max-height="380" class="strategy-table">
        <template #empty>没有匹配的群</template>
        <el-table-column label="群" min-width="150">
          <template #default="{ row }">
            <strong class="name">{{ row.groupName || row.groupId }}</strong>
            <span v-if="row.groupName" class="tk-mono sub">{{ row.groupId }}</span>
          </template>
        </el-table-column>
        <el-table-column label="账号" min-width="100">
          <template #default="{ row }">
            <span class="tk-mono" :title="platformText(row.platform)">{{ row.account }}</span>
          </template>
        </el-table-column>
        <el-table-column label="策略" width="84" align="right">
          <template #default="{ row }">
            <el-tag
              size="small"
              :effect="row.strategy === 'deterministic' ? 'dark' : 'plain'"
              disable-transitions
            >
              {{ strategyLabel(row.strategy) }}
            </el-tag>
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
</style>
