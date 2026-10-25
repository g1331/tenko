import { request } from './client'
import type {
  AccountAction,
  AccountActionBody,
  AccountActionResult,
  AccountsData,
  AccountUpdateBody,
  AccountUpdateResult,
  BlacklistBody,
  BlacklistData,
  StatsData,
  StatsDays,
  GroupResponseBody,
  GroupResponseResult,
  InboxAction,
  InboxData,
  AuthState,
  ChatConversationsData,
  ChatMessagesData,
  ChatSendBody,
  SandboxChat,
  SandboxData,
  SandboxSendBody,
  CoreConfigData,
  FeaturesData,
  FeatureToggleBody,
  FeatureToggleResult,
  LoginBody,
  LoginResult,
  InstalledData,
  LogData,
  LogQuery,
  ManagedAccountsData,
  MarketData,
  OverviewData,
  PairingActionBody,
  PairingInfo,
  PluginConfigData,
  PluginDetail,
  PluginTask,
  PluginTaskBody,
  PluginToggleBody,
  PluginToggleResult,
  RevokeOthersResult,
  SessionsData,
  SettingsBody,
  SettingsData,
  ReleaseNote,
  UpgradeCheck,
  UpgradeJob,
  UpgradeStartBody,
  UpgradeStatus,
} from './types'

export { ApiError, notifyUnauthorized, setUnauthorizedHandler } from './client'
export * from './types'

const segment = encodeURIComponent

/** 全部后端接口的类型化入口；页面只通过它访问网络。 */
export const api = {
  auth: {
    state: () => request<AuthState>('/auth/state'),
    login: (body: LoginBody) => request<LoginResult>('/auth/login', { body }),
    logout: () => request<Record<string, never>>('/auth/logout', { body: {} }),
    sessions: () => request<SessionsData>('/auth/sessions'),
    revokeSession: (id: string) =>
      request<Record<string, never>>(`/auth/sessions/${segment(id)}/revoke`, { body: {} }),
    revokeOthers: () => request<RevokeOthersResult>('/auth/sessions/revoke-others', { body: {} }),
    updateAccount: (body: AccountUpdateBody) =>
      request<AccountUpdateResult>('/auth/account', { body }),
  },
  overview: () => request<OverviewData>('/overview'),
  accounts: () => request<AccountsData>('/accounts'),
  features: () => request<FeaturesData>('/features'),
  market: (refresh = false) =>
    request<MarketData>('/market', { query: { refresh: refresh ? 1 : undefined } }),
  installedPlugins: () => request<InstalledData>('/plugins/installed'),
  pluginDetail: (plugin: string) => request<PluginDetail>(`/plugins/${segment(plugin)}/detail`),
  manage: {
    accounts: () => request<ManagedAccountsData>('/manage/accounts'),
    accountAction: (platform: string, id: string, action: AccountAction, body: AccountActionBody) =>
      request<AccountActionResult>(
        `/manage/accounts/${segment(platform)}/${segment(id)}/${action}`,
        { body },
      ),
    setGroupResponse: (groupId: string, body: GroupResponseBody) =>
      request<GroupResponseResult>(`/manage/groups/${segment(groupId)}/response`, { body }),
    pairing: () => request<PairingInfo>('/manage/pairing'),
    pairingAction: (body: PairingActionBody) => request<PairingInfo>('/manage/pairing', { body }),
    togglePlugin: (plugin: string, body: PluginToggleBody) =>
      request<PluginToggleResult>(`/manage/plugins/${segment(plugin)}`, { body }),
    toggleFeature: (plugin: string, body: FeatureToggleBody) =>
      request<FeatureToggleResult>(`/manage/features/${segment(plugin)}`, { body }),
    pluginTask: (body: PluginTaskBody) => request<PluginTask>('/manage/plugin-tasks', { body }),
    pluginTaskState: (id: string) => request<PluginTask>(`/manage/plugin-tasks/${segment(id)}`),
    pluginConfig: (plugin: string) =>
      request<PluginConfigData>(`/manage/plugin-config/${segment(plugin)}`),
    savePluginConfig: (plugin: string, values: Record<string, unknown>, reset: string[] = []) =>
      request<PluginConfigData>(`/manage/plugin-config/${segment(plugin)}`, {
        body: { values, reset, confirm: true },
      }),
    restart: () => request<{ restarting: boolean }>('/manage/restart', { body: { confirm: true } }),
    upgrade: () => request<UpgradeStatus>('/manage/upgrade'),
    checkUpgrade: () => request<UpgradeCheck>('/manage/upgrade/check', { body: {} }),
    upgradeNotes: () => request<{ note: ReleaseNote | null }>('/manage/upgrade/notes'),
    startUpgrade: (body: UpgradeStartBody) => request<UpgradeJob>('/manage/upgrade', { body }),
    config: () => request<CoreConfigData>('/manage/config'),
    saveConfig: (values: Record<string, unknown>) =>
      request<CoreConfigData>('/manage/config', { body: { values } }),
    settings: () => request<SettingsData>('/manage/settings'),
    saveSettings: (body: SettingsBody) => request<SettingsData>('/manage/settings', { body }),
    inbox: () => request<InboxData>('/manage/inbox'),
    inboxAction: (id: string, action: InboxAction, comment = '') =>
      request<InboxData>(`/manage/inbox/${segment(id)}`, {
        body: { action, confirm: true, comment },
      }),
    logs: (query: LogQuery = {}) => request<LogData>('/manage/logs', { query: { ...query } }),
    chatConversations: (platform: string, account: string) =>
      request<ChatConversationsData>('/manage/chat/conversations', {
        query: { platform, account },
      }),
    chatMessages: (
      platform: string,
      account: string,
      channel: string,
      cursor: { before?: number; after?: number } = {},
    ) =>
      request<ChatMessagesData>('/manage/chat/messages', {
        query: { platform, account, channel, ...cursor },
      }),
    chatSend: (body: ChatSendBody) => request<{ sent: boolean }>('/manage/chat/send', { body }),
    sandbox: (chat: SandboxChat) => request<SandboxData>('/manage/sandbox', { query: { chat } }),
    sandboxSend: (body: SandboxSendBody) =>
      request<{ sent: boolean }>('/manage/sandbox/send', { body }),
    sandboxClear: () => request<{ cleared: boolean }>('/manage/sandbox/clear', { body: {} }),
    sandboxResetData: () => request<{ reset: boolean }>('/manage/sandbox/reset-data', { body: {} }),
    blacklist: () => request<BlacklistData>('/manage/blacklist'),
    setBlacklist: (body: BlacklistBody) => request<BlacklistData>('/manage/blacklist', { body }),
    stats: (days: StatsDays) => request<StatsData>('/manage/stats', { query: { days } }),
    statsReplay: (id: number) =>
      request<{ chat: SandboxChat }>('/manage/stats/replay', { body: { id } }),
  },
}
