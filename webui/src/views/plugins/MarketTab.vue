<script setup lang="ts">
import { computed, ref } from 'vue'
import type { MarketPlugin } from '@/api'
import ResourceBoundary from '@/components/ResourceBoundary.vue'
import { usePluginOps } from '@/composables/usePluginOps'
import CategoryTag, { categoryColor } from './CategoryTag.vue'
import MarketDrawer from './MarketDrawer.vue'
import PluginIcon from './PluginIcon.vue'
import TierTag from './TierTag.vue'

// 插件市场：目录里的插件卡片，点开详情抽屉后可安装。
const ops = usePluginOps()
const query = ref('')
const tier = ref('')
const category = ref('')
const refreshing = ref(false)
const selectedId = ref<string | null>(null)
const drawerOpen = ref(false)

// 抽屉按 id 取最新数据，任务结束刷新目录后安装状态随之更新。
const selected = computed(
  () => ops.market.data.value?.plugins.find((item) => item.id === selectedId.value) ?? null,
)
/** 按目录给出的顺序列出有插件的分类，附带数量。 */
const categories = computed(() => {
  const data = ops.market.data.value
  const count = (name: string) => data?.plugins.filter((item) => item.category === name).length ?? 0
  return (data?.categories ?? [])
    .map((name) => ({ name, count: count(name) }))
    .filter((c) => c.count)
})
const filtered = (list: MarketPlugin[]) => {
  const text = query.value.trim().toLowerCase()
  return list.filter(
    (item) =>
      (!tier.value || item.tier === tier.value) &&
      (!category.value || item.category === category.value) &&
      (!text ||
        [item.name, item.id, item.description, item.category ?? '', ...item.tags]
          .join('\n')
          .toLowerCase()
          .includes(text)),
  )
}

function open(item: MarketPlugin) {
  selectedId.value = item.id
  drawerOpen.value = true
}
async function refresh() {
  refreshing.value = true
  try {
    await ops.loadMarket(true)
  } finally {
    refreshing.value = false
  }
}
</script>

<template>
  <div class="toolbar">
    <el-input
      v-model="query"
      class="search"
      clearable
      placeholder="搜索名称、标识、简介或标签"
      aria-label="搜索市场插件"
    />
    <el-select
      v-model="tier"
      class="filter"
      clearable
      placeholder="全部等级"
      aria-label="按等级筛选"
    >
      <el-option label="官方" value="official" />
      <el-option label="已收录" value="listed" />
    </el-select>
    <el-button class="refresh" :loading="refreshing" @click="refresh">刷新目录</el-button>
  </div>
  <div v-if="categories.length" class="categories" role="group" aria-label="按分类筛选">
    <button
      type="button"
      class="chip"
      :class="{ on: !category }"
      :aria-pressed="!category"
      @click="category = ''"
    >
      全部<span class="count">{{ ops.market.data.value?.plugins.length ?? 0 }}</span>
    </button>
    <button
      v-for="item in categories"
      :key="item.name"
      type="button"
      class="chip"
      :class="{ on: category === item.name }"
      :style="{ '--c': categoryColor(item.name, ops.market.data.value?.categories ?? []) }"
      :aria-pressed="category === item.name"
      @click="category = category === item.name ? '' : item.name"
    >
      <span class="dot" />{{ item.name }}<span class="count">{{ item.count }}</span>
    </button>
  </div>

  <ResourceBoundary :resource="ops.market" label="插件目录" :retry="() => ops.loadMarket()">
    <template #default="{ data }">
      <el-empty
        v-if="!data.available"
        :image-size="64"
        :description="`插件目录暂不可用：${data.error ?? '未知错误'}`"
      >
        <el-button size="small" :loading="refreshing" @click="refresh">重试</el-button>
      </el-empty>
      <el-empty v-else-if="!data.plugins.length" :image-size="64" description="目录里暂无插件" />
      <el-empty
        v-else-if="!filtered(data.plugins).length"
        :image-size="64"
        description="没有匹配的插件"
      />
      <div v-else class="grid">
        <button
          v-for="item in filtered(data.plugins)"
          :key="item.id"
          type="button"
          class="market-card"
          :data-plugin="item.id"
          @click="open(item)"
        >
          <span class="head">
            <PluginIcon :name="item.name || item.id" :src="item.icon_url" />
            <span class="title">
              <strong class="name">{{ item.name }}</strong>
              <code class="key">{{ item.id }}</code>
            </span>
            <TierTag :tier="item.tier" />
          </span>
          <span class="desc">{{ item.description || '暂无简介' }}</span>
          <span v-if="item.category || item.tags.length" class="meta">
            <CategoryTag v-if="item.category" :name="item.category" />
            <span v-for="tag in item.tags.slice(0, 3)" :key="tag" class="tag">#{{ tag }}</span>
          </span>
          <span class="foot">
            <span v-if="item.latest" class="version">最新 {{ item.latest }}</span>
            <span v-else class="problem">{{ item.problem }}</span>
            <el-tag
              v-if="item.installed_version"
              size="small"
              type="success"
              effect="plain"
              disable-transitions
            >
              已安装 {{ item.installed_version }}
            </el-tag>
            <el-tag
              v-if="item.blocked"
              size="small"
              type="danger"
              effect="plain"
              disable-transitions
            >
              已封禁
            </el-tag>
          </span>
        </button>
      </div>
    </template>
  </ResourceBoundary>

  <MarketDrawer v-model="drawerOpen" :plugin="selected" />
</template>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  margin-bottom: 16px;
}
.search {
  width: 260px;
  max-width: 100%;
}
.filter {
  width: 130px;
}
.refresh {
  margin-left: auto;
}
.categories {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: -4px 0 16px;
}
.chip {
  --c: var(--tk-primary);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border: 1px solid var(--tk-line);
  border-radius: 999px;
  background: var(--tk-surface);
  color: var(--tk-text);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
  transition:
    border-color 120ms ease,
    background-color 120ms ease;
}
.chip:hover {
  border-color: var(--c);
}
.chip.on {
  border-color: var(--c);
  background: color-mix(in srgb, var(--c) 12%, var(--tk-surface));
  color: color-mix(in srgb, var(--c) 72%, var(--tk-heading));
  font-weight: 600;
}
.chip:focus-visible {
  outline: 2px solid var(--c);
  outline-offset: 2px;
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--c);
}
.count {
  color: var(--tk-muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.chip.on .count {
  color: inherit;
  opacity: 0.75;
}
.meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 10px;
  min-width: 0;
}
.tag {
  color: var(--tk-muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 300px), 1fr));
  gap: 16px;
}
.market-card {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 10px;
  padding: 16px 18px;
  border: 1px solid var(--tk-line);
  border-radius: var(--tk-radius);
  background: var(--tk-surface);
  box-shadow: var(--tk-shadow);
  color: var(--tk-text);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color 120ms ease;
}
.market-card:hover {
  border-color: var(--tk-primary);
}
.head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.title {
  display: block;
  min-width: 0;
  flex: 1;
}
.name {
  display: block;
  color: var(--tk-heading);
  font-size: 15px;
  overflow-wrap: anywhere;
}
.key {
  display: block;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.desc {
  display: -webkit-box;
  min-height: 2.9em;
  overflow: hidden;
  font-size: 13px;
  line-height: 1.45;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow-wrap: anywhere;
}
.foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 8px;
  margin-top: auto;
  padding-top: 10px;
  border-top: 1px solid var(--tk-line);
  font-size: 12px;
}
.version {
  color: var(--tk-muted);
}
.problem {
  color: var(--tk-warn);
}

@media (max-width: 520px) {
  .categories {
    flex-wrap: nowrap;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .chip {
    flex: none;
  }
  .search,
  .filter {
    width: 100%;
  }
  .grid {
    gap: 12px;
  }
}
</style>
