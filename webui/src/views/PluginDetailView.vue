<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, ApiError, type PluginCommandDoc, type PluginDetail, type PluginLink } from '@/api'
import PageContainer from '@/components/PageContainer.vue'
import PagePanel from '@/components/PagePanel.vue'
import StatusTag from '@/components/StatusTag.vue'
import { confirmAction } from '@/composables/confirm'
import { timeText } from '@/composables/format'
import { featureStatus, maintenanceLocked, toggleLocked } from '@/composables/status'
import { resource, usePanelData } from '@/composables/usePanelData'
import { usePluginActions } from '@/composables/usePluginActions'
import { providePluginOps } from '@/composables/usePluginOps'
import PluginBanners from './plugins/PluginBanners.vue'
import PluginConfigDrawer from './plugins/PluginConfigDrawer.vue'
import PluginIcon from './plugins/PluginIcon.vue'

// 插件详情：说明资料（命令、版本、作者、链接）来自详情接口；状态、开关和操作沿用
// 面板共享的插件数据，实时推送更新后这里跟着变。
const route = useRoute()
const { features, refresh } = usePanelData()
const ops = providePluginOps()
const detail = resource<PluginDetail>()
const configOpen = ref(false)
const { busy, toggle, maintenance, globalToggle, resetGlobal } = usePluginActions(() =>
  Promise.all([refresh(), ops.loadInstalled(), load()]),
)

const pluginId = computed(() => String(route.params.plugin ?? ''))
const feature = computed(
  () => features.data.value?.features.find((item) => item.plugin === pluginId.value) ?? null,
)
const notFound = computed(() => detail.error.value?.status === 404)

async function load() {
  const id = pluginId.value
  if (!id) return
  try {
    const data = await api.pluginDetail(id)
    if (id !== pluginId.value) return
    detail.data.value = data
    detail.error.value = null
  } catch (error) {
    if (id !== pluginId.value) return
    detail.error.value = error instanceof ApiError ? error : new ApiError((error as Error).message)
  } finally {
    if (id === pluginId.value) detail.loading.value = false
  }
}
watch(
  pluginId,
  () => {
    detail.data.value = null
    detail.loading.value = true
    void load()
  },
  { immediate: true },
)
// 顶部的任务进度和重启提醒依赖已安装插件数据；更新完成后版本和链接也要重读。
onMounted(() => ops.loadInstalled())
watch(
  () => ops.task.value?.state,
  (state) => state === 'succeeded' && void load(),
)

// ------------------------------------------------------------------ 页首

const status = computed(() => (feature.value ? featureStatus(feature.value) : null))
const TRUST: Record<string, string> = { official: '官方', listed: '已收录', unlisted: '未收录' }
const sourceText = computed(() => {
  const data = detail.data.value
  if (!data) return ''
  if (data.source === 'builtin') return '内置'
  if (data.source === 'unmanaged') return '未登记的原生插件'
  const trust = data.installed?.trust ?? data.tier
  return trust ? `第三方 · ${TRUST[trust] ?? trust}` : '第三方'
})
const groupStats = computed(() => {
  const values = Object.values(feature.value?.groups ?? {})
  return { on: values.filter(Boolean).length, total: values.length }
})
const scopeText = computed(() => {
  if (!feature.value) return ''
  if (feature.value.scope === 'global') return '全局功能'
  const { on, total } = groupStats.value
  return total ? `群级 · ${on} / ${total} 个群开启` : '群级 · 暂无群组'
})

async function update() {
  const data = detail.data.value
  if (!data?.update) return
  const ok = await confirmAction(
    `将把 ${data.name} 从 ${data.version} 更新到 ${data.update}，在后台进行，完成后需要重启才会生效。`,
    '更新插件',
    '更新',
  )
  if (ok) await ops.run({ action: 'update', plugin_id: data.plugin })
}

// ------------------------------------------------------------------ 命令

/** 说明单独成段，权限做成标签，其余字段（用法、示例、选项）按行列出。 */
function splitCommand(command: PluginCommandDoc) {
  const pick = (label: string) => command.fields.find((field) => field.label === label)?.value
  return {
    description: pick('说明'),
    permission: pick('权限'),
    rows: command.fields.filter((field) => field.label !== '说明' && field.label !== '权限'),
  }
}
const commands = computed(() =>
  (detail.data.value?.commands ?? []).map((command) => ({
    title: command.title,
    ...splitCommand(command),
  })),
)

// ------------------------------------------------------------------ 信息与链接

const installedAt = computed(() => {
  const installed = detail.data.value?.installed
  return installed ? timeText(installed.installed_at) : ''
})
const updatedAt = computed(() => {
  const installed = detail.data.value?.installed
  return installed && installed.updated_at !== installed.installed_at
    ? timeText(installed.updated_at)
    : ''
})

/** 链接只显示主机和路径，协议和末尾斜杠去掉。 */
function linkText(link: PluginLink) {
  try {
    const url = new URL(link.url)
    return `${url.host}${url.pathname}`.replace(/\/$/, '')
  } catch {
    return link.url
  }
}
const LINK_ICONS: Record<PluginLink['kind'], string> = {
  home: 'M219.31,108.68l-80-80a16,16,0,0,0-22.62,0l-80,80A15.87,15.87,0,0,0,32,120v96a8,8,0,0,0,8,8h64a8,8,0,0,0,8-8V160h32v56a8,8,0,0,0,8,8h64a8,8,0,0,0,8-8V120A15.87,15.87,0,0,0,219.31,108.68ZM208,208H160V152a8,8,0,0,0-8-8H104a8,8,0,0,0-8,8v56H48V120l80-80,80,80Z',
  repo: 'M216,104v8a56.06,56.06,0,0,1-48.44,55.47A39.8,39.8,0,0,1,176,192v40a8,8,0,0,1-8,8H104a8,8,0,0,1-8-8V216H72a40,40,0,0,1-40-40A24,24,0,0,0,8,152a8,8,0,0,1,0-16,40,40,0,0,1,40,40,24,24,0,0,0,24,24H96v-8a39.8,39.8,0,0,1,8.44-24.53A56.06,56.06,0,0,1,56,112v-8a58.14,58.14,0,0,1,7.69-28.32A59.78,59.78,0,0,1,69.07,28,8,8,0,0,1,76,24a59.75,59.75,0,0,1,48,24h24a59.75,59.75,0,0,1,48-24,8,8,0,0,1,6.93,4,59.74,59.74,0,0,1,5.37,47.68A58,58,0,0,1,216,104Z',
  pypi: 'M223.68,66.15,135.68,18a15.88,15.88,0,0,0-15.36,0l-88,48.17a16,16,0,0,0-8.32,14v95.64a16,16,0,0,0,8.32,14l88,48.17a15.88,15.88,0,0,0,15.36,0l88-48.17a16,16,0,0,0,8.32-14V80.18A16,16,0,0,0,223.68,66.15ZM128,32l80.34,44-29.77,16.3-80.35-44ZM128,120,47.66,76l33.9-18.56,80.34,44ZM40,90l80,43.78v85.79L40,175.82Zm176,85.78h0l-80,43.79V133.82l32-17.51V152a8,8,0,0,0,16,0V107.55L216,90v85.77Z',
  link: 'M137.54,186.36a8,8,0,0,1,0,11.31l-9.94,10A56,56,0,0,1,48.38,128.4L72.5,104.28A56,56,0,0,1,149.31,102a8,8,0,1,1-10.64,12,40,40,0,0,0-54.85,1.63L59.7,139.72a40,40,0,0,0,56.58,56.58l9.94-9.94A8,8,0,0,1,137.54,186.36Zm70.08-138a56.08,56.08,0,0,0-79.22,0l-9.94,9.95a8,8,0,0,0,11.32,11.31l9.94-9.94a40,40,0,0,1,56.58,56.58L172.18,140.4A40,40,0,0,1,117.33,142,8,8,0,1,0,106.69,154a56,56,0,0,0,76.81-2.26l24.12-24.12A56.08,56.08,0,0,0,207.62,48.38Z',
}
const switchesLink = computed(() => ({
  name: 'switches',
  query: { view: 'plugin', id: pluginId.value },
}))
</script>

<template>
  <PageContainer>
    <nav class="crumbs" aria-label="位置">
      <RouterLink :to="{ name: 'plugins' }" class="back">插件</RouterLink>
      <span aria-hidden="true">/</span>
      <span class="here">{{ detail.data.value?.name ?? feature?.name ?? pluginId }}</span>
    </nav>

    <PluginBanners />

    <el-result
      v-if="notFound"
      icon="warning"
      title="找不到这个插件"
      :sub-title="`没有标识为 ${pluginId} 的插件，它可能已被卸载。`"
    >
      <template #extra>
        <RouterLink :to="{ name: 'plugins' }">
          <el-button type="primary">返回插件列表</el-button>
        </RouterLink>
      </template>
    </el-result>

    <template v-else-if="detail.data.value">
      <section class="hero" :data-plugin="detail.data.value.plugin">
        <PluginIcon
          :name="detail.data.value.name"
          :src="detail.data.value.icon.url"
          :glyph="detail.data.value.icon.glyph"
          :size="64"
          solid
        />
        <div class="hero-main">
          <h2>{{ detail.data.value.name }}</h2>
          <p class="ident">
            <code>{{ detail.data.value.plugin }}</code>
            <span v-if="detail.data.value.version" class="ver">
              v{{ detail.data.value.version }}
            </span>
          </p>
          <p v-if="detail.data.value.description" class="desc">
            {{ detail.data.value.description }}
          </p>
          <div class="chips">
            <StatusTag v-if="status" :kind="status.kind">{{ status.label }}</StatusTag>
            <span class="chip">{{ sourceText }}</span>
            <span v-if="scopeText" class="chip">{{ scopeText }}</span>
            <span v-if="detail.data.value.update" class="chip warn">
              可更新到 v{{ detail.data.value.update }}
            </span>
            <span v-if="detail.data.value.yanked" class="chip danger">
              当前版本已撤回：{{ detail.data.value.yanked }}
            </span>
            <span v-if="detail.data.value.blocked" class="chip danger">
              已被目录封禁：{{ detail.data.value.blocked }}
            </span>
          </div>
        </div>
        <div v-if="feature" class="hero-actions">
          <el-button v-if="detail.data.value.has_config" @click="configOpen = true">配置</el-button>
          <el-button :disabled="maintenanceLocked(feature) || !!busy" @click="maintenance(feature)">
            {{ feature.maintenance ? '解除维护' : '进入维护' }}
          </el-button>
          <el-button :disabled="toggleLocked(feature) || !!busy" @click="toggle(feature)">
            {{ feature.enabled ? '停用' : '启用' }}
          </el-button>
          <el-button
            v-if="detail.data.value.update"
            type="primary"
            :disabled="ops.busy.value"
            @click="update"
          >
            更新
          </el-button>
        </div>
      </section>

      <div class="layout">
        <PagePanel title="命令" class="commands-panel">
          <template v-if="commands.length" #extra>
            {{ commands.length }} 条 · 与群里的 /帮助 一致
          </template>
          <el-empty
            v-if="!commands.length"
            :image-size="56"
            :description="
              feature && !feature.loaded
                ? '插件未加载，加载后才能读取它的命令'
                : '该插件没有注册命令'
            "
          />
          <ol v-else class="commands">
            <li v-for="command in commands" :key="command.title">
              <div class="cmd-head">
                <code class="cmd">{{ command.title }}</code>
                <span
                  v-if="command.permission"
                  class="perm"
                  :class="{ raised: command.permission !== '成员' }"
                  >{{ command.permission }}</span
                >
              </div>
              <p v-if="command.description" class="cmd-desc">{{ command.description }}</p>
              <dl v-if="command.rows.length" class="cmd-rows">
                <template v-for="row in command.rows" :key="row.label">
                  <dt>{{ row.label }}</dt>
                  <dd :class="{ mono: row.label !== '选项' }">{{ row.value }}</dd>
                </template>
              </dl>
            </li>
          </ol>
        </PagePanel>

        <aside class="side">
          <PagePanel title="信息">
            <dl class="facts">
              <dt>版本</dt>
              <dd>
                <template v-if="detail.data.value.version">
                  <span class="num">v{{ detail.data.value.version }}</span>
                  <span v-if="detail.data.value.update" class="num next">
                    → v{{ detail.data.value.update }}
                  </span>
                  <span v-if="detail.data.value.source === 'builtin'" class="hint">
                    随 Tenko 发布
                  </span>
                </template>
                <template v-else>—</template>
              </dd>
              <dt>作者</dt>
              <dd>{{ detail.data.value.authors.join('、') || '—' }}</dd>
              <dt>许可证</dt>
              <dd>{{ detail.data.value.license || '—' }}</dd>
              <template v-if="detail.data.value.installed">
                <dt>安装包</dt>
                <dd class="mono">{{ detail.data.value.installed.requirement }}</dd>
                <dt>安装于</dt>
                <dd>{{ installedAt }}</dd>
                <template v-if="updatedAt">
                  <dt>更新于</dt>
                  <dd>{{ updatedAt }}</dd>
                </template>
              </template>
              <template v-if="detail.data.value.tags.length">
                <dt>标签</dt>
                <dd class="tags">
                  <span v-for="tag in detail.data.value.tags" :key="tag" class="chip">{{
                    tag
                  }}</span>
                </dd>
              </template>
            </dl>
          </PagePanel>

          <PagePanel v-if="detail.data.value.links.length" title="链接">
            <ul class="links">
              <li v-for="link in detail.data.value.links" :key="link.url">
                <a :href="link.url" target="_blank" rel="noopener noreferrer">
                  <svg viewBox="0 0 256 256" aria-hidden="true">
                    <path :d="LINK_ICONS[link.kind]" />
                  </svg>
                  <span class="link-label">{{ link.label }}</span>
                  <span class="link-url">{{ linkText(link) }}</span>
                  <span class="link-out" aria-hidden="true">↗</span>
                </a>
              </li>
            </ul>
          </PagePanel>

          <PagePanel v-if="feature" title="群开关">
            <template v-if="feature.scope !== 'global'" #extra>
              <RouterLink :to="switchesLink" class="to-switches">在开关页管理 →</RouterLink>
            </template>
            <p v-if="feature.maintenance" class="note warn">维护中：所有群暂停响应。</p>
            <template v-if="feature.scope === 'global'">
              <p class="note">全局功能，对所有群统一生效。</p>
              <div class="switch-row">
                <span>当前</span>
                <b>{{ feature.global_enabled === false ? '已关闭' : '已开启' }}</b>
              </div>
              <el-button
                size="small"
                :disabled="feature.protected || !!busy"
                @click="globalToggle(feature)"
              >
                {{ feature.global_enabled === false ? '开启全局功能' : '关闭全局功能' }}
              </el-button>
            </template>
            <template v-else>
              <template v-if="feature.global_enabled != null">
                <p class="note">
                  已被全局覆盖为{{ feature.global_enabled ? '开启' : '关闭' }}，各群开关暂不生效。
                </p>
                <el-button
                  size="small"
                  :disabled="feature.protected || !!busy"
                  @click="resetGlobal(feature)"
                >
                  恢复群级开关
                </el-button>
              </template>
              <template v-else-if="groupStats.total">
                <div
                  class="meter"
                  role="img"
                  :aria-label="`${groupStats.on} / ${groupStats.total} 个群开启`"
                >
                  <span v-if="groupStats.on" :style="{ flexGrow: groupStats.on }" class="on" />
                  <span
                    v-if="groupStats.total - groupStats.on"
                    :style="{ flexGrow: groupStats.total - groupStats.on }"
                    class="off"
                  />
                </div>
                <div class="switch-row">
                  <span>开启</span><b class="num">{{ groupStats.on }}</b>
                </div>
                <div class="switch-row">
                  <span>关闭</span><b class="num">{{ groupStats.total - groupStats.on }}</b>
                </div>
              </template>
              <p v-else class="note">还没有已知的群。</p>
            </template>
          </PagePanel>
        </aside>
      </div>
    </template>

    <el-skeleton v-else-if="detail.loading.value" :rows="6" animated />
    <el-empty
      v-else
      :image-size="64"
      :description="`插件详情暂不可用：${detail.error.value?.message ?? ''}`"
    >
      <el-button @click="load">重试</el-button>
    </el-empty>

    <PluginConfigDrawer
      v-if="detail.data.value?.has_config"
      v-model="configOpen"
      :plugin-id="detail.data.value.plugin"
      :name="detail.data.value.name"
    />
  </PageContainer>
</template>

<style scoped>
.crumbs {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  color: var(--tk-muted);
  font-size: 13px;
}
.back {
  color: var(--tk-primary);
  text-decoration: none;
}
.back::before {
  content: '← ';
}
.back:hover {
  text-decoration: underline;
}
.here {
  color: var(--tk-text);
}

/* 页首 */
.hero {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 4px 22px;
  align-items: start;
  margin-bottom: 18px;
  padding: 24px 26px;
  border: 1px solid var(--tk-line);
  border-radius: var(--tk-radius);
  background: var(--tk-surface);
  box-shadow: var(--tk-shadow);
}
.hero-main {
  min-width: 0;
}
.hero h2 {
  margin: 2px 0 4px;
  color: var(--tk-heading);
  font-size: 24px;
  font-weight: 700;
  letter-spacing: 0.02em;
  overflow-wrap: anywhere;
}
.ident {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0 8px;
  margin: 0;
  color: var(--tk-muted);
  font-size: 13px;
}
.ident code {
  font-family: var(--tk-font-mono);
  font-size: 12.5px;
}
.ver::before {
  margin-right: 8px;
  content: '·';
}
.ver,
.num {
  font-family: var(--tk-font-num);
  font-variant-numeric: tabular-nums;
}
.desc {
  max-width: 68ch;
  margin: 12px 0 0;
  color: var(--tk-text);
  font-size: 14px;
  line-height: 1.65;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 14px;
}
.chip {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 9px;
  border: 1px solid var(--tk-line);
  border-radius: 6px;
  color: var(--tk-muted);
  font-size: 12px;
  white-space: nowrap;
}
.chip.warn {
  border-color: transparent;
  background: var(--tk-warn-bg);
  color: var(--tk-warn);
  font-weight: 600;
}
.chip.danger {
  border-color: transparent;
  background: var(--tk-danger-bg);
  color: var(--tk-danger);
  white-space: normal;
}
.hero-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}
.hero-actions .el-button {
  margin: 0;
}

/* 主体两栏 */
.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(260px, 320px);
  gap: 18px;
  align-items: start;
}
.side {
  display: grid;
  gap: 18px;
}

/* 命令 */
.commands {
  margin: 0;
  padding: 0;
  list-style: none;
}
.commands li {
  padding: 16px 0;
  border-top: 1px solid var(--tk-line);
}
.commands li:first-child {
  padding-top: 4px;
  border-top: 0;
}
.commands li:last-child {
  padding-bottom: 2px;
}
.cmd-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.cmd {
  padding: 4px 10px;
  border: 1px solid var(--tk-line);
  border-radius: 7px;
  background: var(--tk-surface-2);
  color: var(--tk-heading);
  font-family: var(--tk-font-mono);
  font-size: 13.5px;
  font-weight: 600;
}
.perm {
  padding: 2px 8px;
  border: 1px solid var(--tk-line);
  border-radius: 6px;
  color: var(--tk-muted);
  font-size: 12px;
}
/* 需要管理员等更高权限的命令 */
.perm.raised {
  border-color: transparent;
  background: var(--tk-warn-bg);
  color: var(--tk-warn);
  font-weight: 600;
}
.cmd-desc {
  margin: 10px 0 0;
  color: var(--tk-text);
  font-size: 14px;
  line-height: 1.6;
}
.cmd-rows {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 6px 16px;
  margin: 10px 0 0;
  font-size: 13px;
}
.cmd-rows dt {
  color: var(--tk-muted);
}
.cmd-rows dd {
  margin: 0;
  color: var(--tk-text);
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.mono {
  font-family: var(--tk-font-mono);
  font-size: 12.5px;
}

/* 信息 */
.facts {
  display: grid;
  grid-template-columns: 4.5em minmax(0, 1fr);
  gap: 12px 12px;
  margin: 0;
  font-size: 13px;
}
.facts dt {
  color: var(--tk-muted);
}
.facts dd {
  margin: 0;
  color: var(--tk-heading);
  overflow-wrap: anywhere;
}
.facts .next {
  margin-left: 4px;
  color: var(--tk-warn);
}
.facts .hint {
  display: block;
  margin-top: 2px;
  color: var(--tk-muted);
  font-size: 12px;
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

/* 链接 */
.links {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.links a {
  display: grid;
  grid-template-columns: 18px auto minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  padding: 10px 12px;
  border: 1px solid var(--tk-line);
  border-radius: 10px;
  color: var(--tk-heading);
  font-size: 13px;
  text-decoration: none;
  transition:
    border-color 0.15s,
    background 0.15s;
}
.links a:hover {
  border-color: var(--tk-primary);
  background: var(--tk-primary-soft);
}
.links a:focus-visible {
  outline: 2px solid var(--tk-primary);
  outline-offset: 2px;
}
.links svg {
  width: 18px;
  height: 18px;
  fill: var(--tk-muted);
}
.link-url {
  overflow: hidden;
  color: var(--tk-muted);
  font-size: 12px;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.link-out {
  color: var(--tk-muted);
}

/* 群开关 */
.to-switches {
  color: var(--tk-primary);
  font-size: 12px;
  text-decoration: none;
}
.to-switches:hover {
  text-decoration: underline;
}
.note {
  margin: 0 0 12px;
  color: var(--tk-muted);
  font-size: 13px;
  line-height: 1.6;
}
.note.warn {
  color: var(--tk-warn);
}
.meter {
  display: flex;
  gap: 3px;
  height: 8px;
  margin-bottom: 14px;
}
.meter span {
  flex-basis: 0;
  border-radius: 4px;
}
.meter .on {
  background: var(--tk-ok-solid);
}
.meter .off {
  background: var(--tk-switch-off);
  opacity: 0.55;
}
.switch-row {
  display: flex;
  justify-content: space-between;
  padding: 6px 0;
  color: var(--tk-text);
  font-size: 13px;
}
.switch-row + .el-button {
  margin-top: 10px;
}

@media (max-width: 960px) {
  .layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 640px) {
  .hero {
    grid-template-columns: auto minmax(0, 1fr);
    padding: 18px;
  }
  .hero-actions {
    grid-column: 1 / -1;
    justify-content: flex-start;
    margin-top: 12px;
  }
  .hero h2 {
    font-size: 20px;
  }
}
</style>
