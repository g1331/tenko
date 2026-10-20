import type { AccountSummary, ManagedAccount } from '@/api'

export const accountKey = (account: Pick<ManagedAccount, 'platform' | 'id'>) =>
  `${account.platform}/${account.id}`

export const accountName = (account: ManagedAccount) => account.alias || account.name || account.id

export const STRATEGY_LABELS: Record<string, string> = { random: '随机', deterministic: '定向' }
export const strategyLabel = (strategy: string) => STRATEGY_LABELS[strategy] ?? '其他'

/** 某账号在各群的响应策略计数；已知策略之外统一归为 other。 */
export function strategyCounts(summary: AccountSummary | undefined) {
  const counts = { random: 0, deterministic: 0, other: 0 }
  for (const item of summary?.response_strategies ?? []) {
    if (item.strategy === 'random') counts.random += 1
    else if (item.strategy === 'deterministic') counts.deterministic += 1
    else counts.other += 1
  }
  return counts
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
