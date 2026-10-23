<script setup lang="ts">
import { computed, onMounted, reactive, ref, shallowRef } from 'vue'
import { api, ApiError, type BlacklistData, type BlacklistEntry, type BlacklistKind } from '@/api'
import PageContainer from '@/components/PageContainer.vue'
import PagePanel from '@/components/PagePanel.vue'
import QqAvatar from '@/components/QqAvatar.vue'
import { confirmAction } from '@/composables/confirm'
import { numberText, platformText, relativeText, timeText } from '@/composables/format'

// 全局黑名单：与群里的 /拉黑、/拉黑群 是同一份名单。面板按 Master 操作，可选平台。
const data = shallowRef<BlacklistData | null>(null)
const loadError = ref<string | null>(null)
const kind = ref<BlacklistKind>('user')
const form = reactive({ platform: 'qq-client', id: '', reason: '' })
const saving = ref(false)
const pending = ref(new Set<string>())

const LABEL = { user: '用户', group: '群' } as const
const errorText = (error: unknown) => (error instanceof ApiError ? error.message : String(error))
const keyOf = (entry: BlacklistEntry) => `${kind.value}:${entry.platform}:${entry.id}`
const entries = computed(
  () => (kind.value === 'user' ? data.value?.users : data.value?.groups) ?? [],
)
const title = (entry: BlacklistEntry) => entry.name || entry.id
const operator = (entry: BlacklistEntry) =>
  entry.operator_id === 'webui' ? '面板' : entry.operator_id || '未知'

async function load() {
  try {
    data.value = await api.manage.blacklist()
    loadError.value = null
  } catch (error) {
    loadError.value = errorText(error)
  }
}
onMounted(load)

async function add() {
  const id = form.id.trim()
  if (!id) return
  saving.value = true
  try {
    data.value = await api.manage.setBlacklist({
      kind: kind.value,
      platform: form.platform,
      id,
      blacklisted: true,
      reason: form.reason.trim(),
    })
    ElMessage.success(`已拉黑${LABEL[kind.value]} ${id}`)
    form.id = ''
    form.reason = ''
  } catch (error) {
    ElMessage.error(`拉黑失败：${errorText(error)}`)
  } finally {
    saving.value = false
  }
}

async function remove(entry: BlacklistEntry) {
  const label = `${LABEL[kind.value]} ${title(entry)}`
  if (!(await confirmAction(`把${label}移出黑名单？`, '解除拉黑', '解除'))) return
  const key = keyOf(entry)
  pending.value = new Set(pending.value).add(key)
  try {
    data.value = await api.manage.setBlacklist({
      kind: kind.value,
      platform: entry.platform,
      id: entry.id,
      blacklisted: false,
    })
    ElMessage.success(`已解除拉黑${label}`)
  } catch (error) {
    ElMessage.error(`解除失败：${errorText(error)}`)
  } finally {
    const rest = new Set(pending.value)
    rest.delete(key)
    pending.value = rest
  }
}
</script>

<template>
  <PageContainer>
    <PagePanel :title="`被拉黑的${LABEL[kind]}`">
      <template #extra>
        <el-radio-group v-model="kind" size="small" aria-label="名单类型">
          <el-radio-button value="user"
            >用户 {{ numberText(data?.users.length ?? 0) }}</el-radio-button
          >
          <el-radio-button value="group"
            >群 {{ numberText(data?.groups.length ?? 0) }}</el-radio-button
          >
        </el-radio-group>
      </template>

      <p class="note">
        {{
          kind === 'user'
            ? '名单里的用户在任何群和私聊都不会得到响应。Master 不能拉黑；BOT 管理员需先撤销 BOT 管理。'
            : '名单里的群对任何人都不响应，公告也会跳过它。通知群不能拉黑。'
        }}
      </p>

      <el-alert v-if="loadError && !data" type="error" :closable="false" show-icon>
        <template #title>读取黑名单失败：{{ loadError }}</template>
        <el-button size="small" @click="load">重试</el-button>
      </el-alert>
      <el-skeleton v-else-if="!data" :rows="3" animated />
      <template v-else>
        <el-alert
          v-if="!data.ready"
          type="warning"
          :closable="false"
          show-icon
          title="权限状态尚未从数据库加载，暂时不能修改黑名单"
        />
        <form class="add" @submit.prevent="add">
          <el-select
            v-model="form.platform"
            class="platform"
            aria-label="平台"
            :disabled="!data.ready"
          >
            <el-option
              v-for="platform in data.platforms"
              :key="platform"
              :value="platform"
              :label="platformText(platform)"
            />
          </el-select>
          <el-input
            v-model="form.id"
            class="id"
            :placeholder="kind === 'user' ? '用户 ID' : '群号'"
            :aria-label="kind === 'user' ? '用户 ID' : '群号'"
            maxlength="128"
            :disabled="!data.ready"
          />
          <el-input
            v-model="form.reason"
            class="reason"
            placeholder="原因（可留空）"
            aria-label="原因"
            maxlength="200"
            :disabled="!data.ready"
          />
          <el-button
            type="danger"
            native-type="submit"
            :loading="saving"
            :disabled="!data.ready || !form.id.trim()"
          >
            拉黑
          </el-button>
        </form>

        <el-empty
          v-if="!entries.length"
          :image-size="56"
          :description="`没有被拉黑的${LABEL[kind]}`"
        />
        <ul v-else class="list">
          <li v-for="entry in entries" :key="keyOf(entry)" class="item">
            <QqAvatar
              :id="entry.id"
              :name="title(entry)"
              :kind="kind"
              :platform="entry.platform"
              :size="36"
            />
            <div class="body">
              <div class="head">
                <strong class="name">{{ title(entry) }}</strong>
                <span v-if="entry.name" class="tk-mono sub">{{ entry.id }}</span>
                <span class="platform-tag">{{ platformText(entry.platform) }}</span>
              </div>
              <p class="reason-text" :class="{ empty: !entry.reason }">
                {{ entry.reason || '未填写原因' }}
              </p>
              <p class="meta">
                操作人 {{ operator(entry) }} ·
                <span :title="timeText(entry.created_at)">{{
                  relativeText(entry.created_at)
                }}</span>
              </p>
            </div>
            <el-button
              size="small"
              :loading="pending.has(keyOf(entry))"
              :disabled="!data.ready"
              @click="remove(entry)"
            >
              解除
            </el-button>
          </li>
        </ul>
      </template>
    </PagePanel>
  </PageContainer>
</template>

<style scoped>
.note {
  margin: 0 0 14px;
  color: var(--tk-muted);
  font-size: 13px;
  line-height: 1.6;
}
.add {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 12px 0 6px;
}
.add .platform {
  width: 130px;
}
.add .id {
  width: 180px;
}
.add .reason {
  flex: 1;
  min-width: 180px;
}
.list {
  display: grid;
  margin: 8px 0 0;
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
.platform-tag {
  padding: 0 6px;
  border: 1px solid var(--tk-line);
  border-radius: 4px;
  color: var(--tk-muted);
  font-size: 11px;
  line-height: 18px;
}
.reason-text,
.meta {
  margin: 4px 0 0;
  font-size: 13px;
  overflow-wrap: anywhere;
}
.reason-text.empty {
  color: var(--tk-muted);
}
.meta {
  color: var(--tk-muted);
  font-size: 12px;
}
@media (max-width: 640px) {
  .add .platform {
    width: 104px;
  }
  .add .id {
    flex: 1;
    width: auto;
  }
  .add .reason,
  .add .el-button {
    flex: 1 1 100%;
  }
}
</style>
