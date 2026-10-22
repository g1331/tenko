<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { MarketPlugin } from '@/api'
import { confirmAction } from '@/composables/confirm'
import { usePluginOps } from '@/composables/usePluginOps'
import PluginIcon from './PluginIcon.vue'
import TierTag from './TierTag.vue'

// 市场插件详情：版本清单与安装。面板只能安装目录里的插件。
const props = defineProps<{ plugin: MarketPlugin | null }>()
const visible = defineModel<boolean>({ required: true })
const ops = usePluginOps()

const version = ref('')
watch(
  () => props.plugin?.id,
  () => (version.value = props.plugin?.latest ?? ''),
  { immediate: true },
)

// 目录里的主页是第三方填的，只放行 http(s) 链接。
const homepage = computed(() =>
  /^https?:\/\//i.test(props.plugin?.homepage ?? '') ? props.plugin!.homepage : '',
)
const canInstall = computed(() => !!version.value && !props.plugin?.blocked && !ops.busy.value)

async function install() {
  const plugin = props.plugin
  if (!plugin || !version.value) return
  const ok = await confirmAction(
    `将安装 ${plugin.name}（${plugin.id}）${version.value}。安装在后台进行，完成后启用即可使用。`,
    '安装插件',
    '安装',
  )
  if (!ok) return
  visible.value = false
  await ops.run({ action: 'install', plugin_id: plugin.id, version: version.value })
}
</script>

<template>
  <el-drawer
    v-model="visible"
    :title="plugin?.name || '插件详情'"
    direction="rtl"
    append-to-body
    size="min(480px, 100vw)"
  >
    <template v-if="plugin">
      <div class="head">
        <PluginIcon :name="plugin.name || plugin.id" :src="plugin.icon_url" />
        <div class="title">
          <code>{{ plugin.id }}</code>
          <div class="tags">
            <TierTag :tier="plugin.tier" />
            <el-tag
              v-if="plugin.installed_version"
              size="small"
              type="success"
              effect="plain"
              disable-transitions
            >
              已安装 {{ plugin.installed_version }}
            </el-tag>
          </div>
        </div>
      </div>

      <el-alert
        v-if="plugin.blocked"
        type="error"
        show-icon
        :closable="false"
        :title="`已被目录封禁：${plugin.blocked}`"
      />
      <p class="desc">{{ plugin.description || '暂无简介' }}</p>

      <dl class="meta">
        <dt>作者</dt>
        <dd>{{ plugin.authors.join('、') || '—' }}</dd>
        <dt>主页</dt>
        <dd>
          <a
            v-if="homepage"
            class="tk-link"
            :href="homepage"
            target="_blank"
            rel="noopener noreferrer"
          >
            {{ homepage }}
          </a>
          <template v-else>—</template>
        </dd>
        <dt>许可证</dt>
        <dd>{{ plugin.license || '—' }}</dd>
        <dt>标签</dt>
        <dd>
          <template v-if="plugin.tags.length">
            <el-tag
              v-for="item in plugin.tags"
              :key="item"
              size="small"
              type="info"
              effect="plain"
              disable-transitions
            >
              {{ item }}
            </el-tag>
          </template>
          <template v-else>—</template>
        </dd>
        <dt>来源</dt>
        <dd class="source">{{ plugin.source_type }} · {{ plugin.source }}</dd>
      </dl>

      <h4>版本</h4>
      <el-radio-group v-if="plugin.versions.length" v-model="version" class="versions">
        <el-radio
          v-for="item in plugin.versions"
          :key="item.version"
          :value="item.version"
          :disabled="!!item.problem || !!plugin.installed_version"
        >
          <span class="v">{{ item.version }}</span>
          <span :class="item.problem ? 'problem' : 'ok'">{{ item.problem ?? '可安装' }}</span>
        </el-radio>
      </el-radio-group>
      <p v-else class="note">目录里还没有版本。</p>

      <p v-if="plugin.installed_version" class="note">
        已安装 {{ plugin.installed_version }}。更新、停用或卸载请到“扩展插件”标签页。
      </p>
      <p class="note">目录之外的来源不能在面板安装，需要 Master 在聊天里使用 /安装插件。</p>
    </template>
    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
      <el-button
        v-if="!plugin?.installed_version"
        type="primary"
        :disabled="!canInstall"
        @click="install"
      >
        {{ ops.busy.value ? '有任务进行中' : version ? `安装 ${version}` : '无可安装版本' }}
      </el-button>
    </template>
  </el-drawer>
</template>

<style scoped>
.head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}
.title {
  display: grid;
  gap: 6px;
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.desc {
  margin: 14px 0;
  color: var(--tk-text);
  font-size: 13px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.meta {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 8px 14px;
  margin: 0 0 18px;
  font-size: 13px;
}
.meta dt {
  color: var(--tk-muted);
}
.meta dd {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 6px;
  margin: 0;
  overflow-wrap: anywhere;
}
.source {
  font-family: var(--tk-font-mono);
  font-size: 12px;
}
h4 {
  margin: 0 0 6px;
  color: var(--tk-heading);
  font-size: 13px;
  letter-spacing: 0.04em;
}
.versions {
  display: grid;
  gap: 2px;
}
.versions :deep(.el-radio) {
  height: auto;
  min-height: 32px;
  margin-right: 0;
  white-space: normal;
}
.v {
  margin-right: 10px;
  font-family: var(--tk-font-mono);
}
.ok {
  color: var(--tk-ok);
  font-size: 12px;
}
.problem {
  color: var(--tk-warn);
  font-size: 12px;
}
.note {
  margin: 12px 0 0;
  color: var(--tk-muted);
  font-size: 12px;
  line-height: 1.65;
}
</style>
