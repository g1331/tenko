<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import PageContainer from '@/components/PageContainer.vue'
import FriendRequestSettings from './settings/FriendRequestSettings.vue'
import CoreConfigSettings from './settings/CoreConfigSettings.vue'
import AccountForm from './settings/AccountForm.vue'
import RuntimeSettings from './settings/RuntimeSettings.vue'
import SessionList from './settings/SessionList.vue'

const route = useRoute()
const security = ref<HTMLElement | null>(null)
const sessions = ref<InstanceType<typeof SessionList> | null>(null)

// 顶栏用户菜单以 ?section=account-security 跳转到这里；等路由自身的回顶滚动结束后再定位。
onMounted(async () => {
  if (route.query.section !== 'account-security') return
  await nextTick()
  requestAnimationFrame(() => security.value?.scrollIntoView({ block: 'start' }))
})
</script>

<template>
  <PageContainer>
    <section class="section" aria-labelledby="sec-runtime">
      <h2 id="sec-runtime">运行设置</h2>
      <div class="stack">
        <RuntimeSettings />
        <FriendRequestSettings />
      </div>
    </section>

    <section class="section" aria-labelledby="sec-core">
      <h2 id="sec-core">宿主配置</h2>
      <CoreConfigSettings />
    </section>

    <section id="account-security" ref="security" class="section" aria-labelledby="sec-security">
      <h2 id="sec-security">账号与安全</h2>
      <div class="grid">
        <AccountForm @updated="sessions?.reload()" />
        <SessionList ref="sessions" />
      </div>
    </section>
  </PageContainer>
</template>

<style scoped>
.section {
  margin-bottom: 28px;
  scroll-margin-top: 12px;
}
.section h2 {
  margin: 0 0 12px;
  color: var(--tk-heading);
  font-size: 16px;
  letter-spacing: 0.04em;
}
.stack {
  display: grid;
  gap: 16px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: start;
  gap: 16px;
}
@media (max-width: 1100px) {
  .grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
