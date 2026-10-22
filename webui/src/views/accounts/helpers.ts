import type { GroupAccount, GroupStatus, ManagedAccount } from '@/api'
import { timeText } from '@/composables/format'

export const accountKey = (account: Pick<ManagedAccount, 'platform' | 'id'>) =>
  `${account.platform}/${account.id}`

export const accountName = (account: ManagedAccount) => account.alias || account.name || account.id

export const STRATEGY_LABELS: Record<string, string> = { random: '随机', deterministic: '定向' }
export const strategyLabel = (strategy: string) => STRATEGY_LABELS[strategy] ?? '其他'

export const ROLE_LABELS: Record<string, string> = { owner: '群主', admin: '管理员' }

/** 群内账号的展示名：昵称优先，没有昵称时用账号 ID。 */
export const memberName = (account: GroupAccount) => account.name || account.id

/** 账号在群内不可用时的说明；可用时返回空字符串。 */
export function memberStateText(account: GroupAccount): string {
  if (account.state === 'disabled') return '已停用'
  if (account.state === 'offline') return '离线'
  if (account.state === 'muted')
    return account.muted_until ? `禁言至 ${timeText(account.muted_until)}` : '被禁言'
  return ''
}

/** 群不会响应的原因，与后端选路判定一致。 */
export function silentText(group: GroupStatus): string {
  if (group.silent_reason === 'target_unavailable') {
    const target = group.accounts.find((item) => item.ref === group.deterministic_account)
    return target
      ? `定向账号 ${memberName(target)} ${memberStateText(target)}，不会改由其他账号响应`
      : '没有指定定向账号'
  }
  return '没有可用账号：绑定的账号都已离线、停用或在本群被禁言'
}

export const CAPABILITY_LABELS: Record<string, string> = {
  send_group_message: '群消息',
  send_private_message: '私聊消息',
  member_mute: '成员禁言',
  group_mute: '全群禁言',
  message_delete: '撤回消息',
  member_kick: '移除成员',
  group_essence: '消息加精',
  group_leave: '退出群组',
  group_invite_approval: '邀请审批',
  friend_request_approval: '好友申请审批',
  group_list: '读取群列表',
}
