import { onScopeDispose, ref } from 'vue'
import { api } from '@/api'

const FIRST_POLL_MS = 3000
const POLL_MS = 2000
const GIVE_UP_MS = 90_000

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

/**
 * 请求重启后轮询 /auth/state，直到面板重新应答。
 * restart() 在面板恢复时返回 true；请求被拒、超时或组件已卸载时返回 false。
 */
export function useRestart() {
  const restarting = ref(false)
  const failed = ref(false)
  let alive = true
  onScopeDispose(() => {
    alive = false
  })

  async function restart(): Promise<boolean> {
    try {
      await api.manage.restart()
    } catch (error) {
      ElMessage.error(`重启失败：${(error as Error).message}`)
      return false
    }
    restarting.value = true
    failed.value = false
    await sleep(FIRST_POLL_MS)
    const deadline = Date.now() + GIVE_UP_MS
    while (alive && Date.now() < deadline) {
      try {
        await api.auth.state()
        restarting.value = false
        ElMessage.success('已重启')
        return true
      } catch {
        await sleep(POLL_MS)
      }
    }
    restarting.value = false
    failed.value = alive
    return false
  }

  return { restarting, failed, restart }
}
