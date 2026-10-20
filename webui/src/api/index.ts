import { request } from './client'
import type {
  AccountAction,
  AccountActionBody,
  AccountActionResult,
  AccountsData,
  AccountUpdateBody,
  AccountUpdateResult,
  AuthState,
  FeaturesData,
  FeatureToggleBody,
  FeatureToggleResult,
  LoginBody,
  LoginResult,
  LogData,
  LogQuery,
  ManagedAccountsData,
  OverviewData,
  PairingActionBody,
  PairingInfo,
  PluginToggleBody,
  PluginToggleResult,
  RevokeOthersResult,
  SessionsData,
  SettingsBody,
  SettingsData,
} from './types'

export { ApiError, setUnauthorizedHandler } from './client'
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
  manage: {
    accounts: () => request<ManagedAccountsData>('/manage/accounts'),
    accountAction: (platform: string, id: string, action: AccountAction, body: AccountActionBody) =>
      request<AccountActionResult>(
        `/manage/accounts/${segment(platform)}/${segment(id)}/${action}`,
        { body },
      ),
    pairing: () => request<PairingInfo>('/manage/pairing'),
    pairingAction: (body: PairingActionBody) => request<PairingInfo>('/manage/pairing', { body }),
    togglePlugin: (plugin: string, body: PluginToggleBody) =>
      request<PluginToggleResult>(`/manage/plugins/${segment(plugin)}`, { body }),
    toggleFeature: (plugin: string, body: FeatureToggleBody) =>
      request<FeatureToggleResult>(`/manage/features/${segment(plugin)}`, { body }),
    settings: () => request<SettingsData>('/manage/settings'),
    saveSettings: (body: SettingsBody) => request<SettingsData>('/manage/settings', { body }),
    logs: (query: LogQuery = {}) => request<LogData>('/manage/logs', { query: { ...query } }),
  },
}
