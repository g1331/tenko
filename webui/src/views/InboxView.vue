<script setup lang="ts">
import { computed, onMounted, ref, shallowRef, watch } from 'vue'
import { api, ApiError, type InboxAction, type InboxItem } from '@/api'
import PageContainer from '@/components/PageContainer.vue'
import PagePanel from '@/components/PagePanel.vue'
import QqAvatar from '@/components/QqAvatar.vue'
import { confirmAction } from '@/composables/confirm'
import { numberText, parseTime, relativeText } from '@/composables/format'
import { usePanelData } from '@/composables/usePanelData'
import { providePluginOps } from '@/composables/usePluginOps'
import PluginAttention from './plugins/PluginAttention.vue'
import PluginBanners from './plugins/PluginBanners.vue'

// 好友申请和群邀请：插件留给 Master 决定的请求，在这里同意、拒绝或忽略；
// 需要处理的插件（停用的、加载失败的）也列在这里。
const { overview, accounts, refresh } = usePanelData()
providePluginOps()

const items = shallowRef<InboxItem[] | null>(null)
const loadError = ref<string | null>(null)
const pending = ref(new Set<string>())

const errorText = (error: unknown) => (error instanceof ApiError ? error.message : String(error))

async function load() {
  try {
    items.value = (await api.manage.inbox()).items
    loadError.value = null
  } catch (error) {
    loadError.value = errorText(error)
  }
}
onMounted(load)
// 实时推送的总览里带着待处理数，变化时重新拉列表。
watch(() => overview.data.value?.inbox, load)

const rows = computed(() => [...(items.value ?? [])].reverse())
const botName = (item: InboxItem) =>
  accounts.data.value?.accounts.find(
    (account) => account.id === item.account_id && account.platform === item.platform,
  )?.name || item.account_id
const who = (item: InboxItem) =>
  item.user_id && item.user_id !== item.user_name
    ? `${item.user_name}（${item.user_id}）`
    : item.user_name
const title = (item: InboxItem) =>
  item.kind === 'group' ? item.group_name || item.group_id || '' : item.user_name

function expiresText(item: InboxItem) {
  const at = parseTime(item.expires_at)
  if (!at) return ''
  const minutes = Math.max(Math.ceil((at.getTime() - Date.now()) / 60_000), 0)
  return `${minutes} 分钟后自动拒绝`
}

async function reason(item: InboxItem): Promise<string | null> {
  try {
    const { value } = await ElMessageBox.prompt(
      `拒绝 ${title(item)} 的入群邀请，理由会发给邀请人（可留空）。`,
      '拒绝群邀请',
      {
        confirmButtonText: '拒绝',
        cancelButtonText: '取消',
        inputPlaceholder: 'BOT拒绝了你的入群邀请!',
        inputValidator: (text) => (text ?? '').length <= 100 || '理由最多 100 字',
      },
    )
    return value ?? ''
  } catch {
    return null
  }
}

async function act(item: InboxItem, action: InboxAction) {
  let comment = ''
  if (action === 'approve') {
    const message =
      item.kind === 'group'
        ? `让 ${botName(item)} 加入 ${title(item)}？`
        : `同意 ${who(item)} 加 ${botName(item)} 为好友？`
    if (
      !(await confirmAction(message, item.kind === 'group' ? '同意入群' : '同意好友申请', '同意'))
    )
      return
  } else if (action === 'reject') {
    if (item.kind === 'group') {
      const text = await reason(item)
      if (text === null) return
      comment = text
    } else if (!(await confirmAction(`拒绝 ${who(item)} 的好友申请？`, '拒绝好友申请', '拒绝'))) {
      return
    }
  }
  pending.value = new Set(pending.value).add(item.id)
  try {
    items.value = (await api.manage.inboxAction(item.id, action, comment)).items
    ElMessage.success({ approve: '已同意', reject: '已拒绝', dismiss: '已从列表移除' }[action])
    void refresh() // 角标和账号的群列表
  } catch (error) {
    ElMessage.error(`处理失败：${errorText(error)}`)
  } finally {
    const rest = new Set(pending.value)
    rest.delete(item.id)
    pending.value = rest
  }
}
</script>

<template>
  <PageContainer>
    <PluginBanners />
    <PluginAttention />
    <PagePanel title="好友申请与群邀请">
      <template v-if="items?.length" #extra>共 {{ numberText(items.length) }} 项</template>
      <el-alert v-if="loadError && !items" type="error" :closable="false" show-icon>
        <template #title>读取待处理列表失败：{{ loadError }}</template>
        <el-button size="small" @click="load">重试</el-button>
      </el-alert>
      <el-skeleton v-else-if="!items" :rows="3" animated />
      <el-empty
        v-else-if="!rows.length"
        :image-size="56"
        description="没有待处理的好友申请或群邀请"
      />
      <ul v-else class="inbox">
        <li v-for="item in rows" :key="item.id" class="item">
          <QqAvatar
            :id="(item.kind === 'group' ? item.group_id : item.user_id) ?? ''"
            :name="title(item)"
            :kind="item.kind === 'group' ? 'group' : 'user'"
            :platform="item.platform"
            :size="40"
          />
          <div class="body">
            <div class="head">
              <strong class="name">{{ title(item) }}</strong>
              <span class="tk-mono sub">{{
                item.kind === 'group' ? item.group_id : item.user_id
              }}</span>
              <span :class="['kind', `is-${item.kind}`]">{{
                item.kind === 'group' ? '群邀请' : '好友申请'
              }}</span>
            </div>
            <p v-if="item.kind === 'group'" class="line">邀请人 {{ who(item) }}</p>
            <p v-if="item.comment" class="comment">{{ item.comment }}</p>
            <p class="meta">
              <span class="bot">
                发给
                <QqAvatar
                  :id="item.account_id"
                  :name="botName(item)"
                  :platform="item.platform"
                  :size="16"
                  kind="user"
                />
                {{ botName(item) }}
              </span>
              <span>{{ relativeText(item.created_at) }}</span>
              <span v-if="item.expires_at" class="expires">{{ expiresText(item) }}</span>
            </p>
          </div>
          <div class="actions">
            <el-button
              type="primary"
              size="small"
              :loading="pending.has(item.id)"
              @click="act(item, 'approve')"
            >
              同意
            </el-button>
            <el-button size="small" :disabled="pending.has(item.id)" @click="act(item, 'reject')">
              拒绝
            </el-button>
            <el-tooltip content="只从列表移除，平台上的请求保持不变" placement="top">
              <el-button
                size="small"
                text
                :disabled="pending.has(item.id)"
                @click="act(item, 'dismiss')"
              >
                忽略
              </el-button>
            </el-tooltip>
          </div>
        </li>
      </ul>
    </PagePanel>
  </PageContainer>
</template>

<style scoped>
.inbox {
  display: grid;
  margin: 0;
  padding: 0;
  list-style: none;
}
.item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 0;
  border-top: 1px solid var(--tk-line);
}
.item:first-child {
  padding-top: 4px;
  border-top: 0;
}
.body {
  flex: 1;
  min-width: 0;
}
.head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 8px;
}
.name {
  color: var(--tk-heading);
  overflow-wrap: anywhere;
}
.sub {
  color: var(--tk-muted);
  font-size: 12px;
}
.kind {
  padding: 0 6px;
  border: 1px solid var(--tk-line);
  border-radius: 4px;
  color: var(--tk-muted);
  font-size: 11px;
  line-height: 18px;
}
.kind.is-group {
  border-color: var(--tk-primary);
  color: var(--tk-primary);
}
.line,
.comment,
.meta {
  margin: 4px 0 0;
  font-size: 13px;
}
.comment {
  padding: 6px 10px;
  border-radius: 6px;
  background: var(--tk-surface-2);
  color: var(--tk-text);
  overflow-wrap: anywhere;
}
.meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 14px;
  color: var(--tk-muted);
  font-size: 12px;
}
.bot {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.expires {
  color: var(--el-color-warning);
}
.actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}
.actions .el-button + .el-button {
  margin-left: 0;
}
@media (max-width: 640px) {
  .item {
    flex-wrap: wrap;
  }
  .actions {
    flex-basis: 100%;
    justify-content: flex-start;
    padding-left: 52px;
  }
}
</style>
