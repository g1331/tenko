/**
 * 由 tenko/webui/contracts.py 生成，不要手工修改。
 * 修改契约后在 webui/ 运行 npm run gen:types 并提交本文件。
 */

/**
 * 没有内容的成功响应。
 */
export type Empty = Record<string, never>

/**
 * 修改用户名和/或密码；二者都不传时后端只校验当前密码。
 */
export interface AccountUpdateBody {
  current_password: string
  username?: string
  new_password?: string
}
export interface AccountUpdateResult {
  username: string
}
export interface AuthState {
  authenticated: boolean
  username: string | null
  password_set: boolean
  expires_at: string | null
}
export interface HostResources {
  cpu_percent: number
  memory_used: number
  memory_total: number
  memory_percent: number
  disk_used: number
  disk_total: number
  disk_percent: number
  net_sent: number
  net_received: number
}
export interface LoginBody {
  username: string
  password: string
}
export interface LoginResult {
  username: string
  expires_at: string
}
export interface MessageCounts {
  received_count: number
  sent_count: number
  /**
   * 最近 60 秒的收发条数。
   */
  received_rate: number
  sent_rate: number
}
export interface OverviewData {
  version: string
  uptime_seconds: number
  /**
   * Tenko 进程常驻内存（字节）；读取不到时为 null。
   */
  rss: number | null
  /**
   * 主机资源（字节 / 百分比）；未安装 psutil 等读取不到时为 null。
   */
  resources: HostResources | null
  /**
   * 近 60 分钟每分钟的 [接收, 发送]，最早的在前，最后一格是当前分钟。
   */
  activity: [number, number][]
  online_accounts: number
  active_groups: number
  /**
   * 待处理的好友申请和群邀请数。
   */
  inbox: number
  /**
   * 需要处理的插件：停用待恢复的和本次启动加载失败的。
   */
  plugin_attention?: number
  messages: MessageCounts
}
export interface RevokeOthersResult {
  revoked: number
}
export interface SessionInfo {
  id: string
  created_at: string
  last_seen_at: string
  expires_at: string
  ip: string
  user_agent: string
  current: boolean
}
export interface SessionsData {
  sessions: SessionInfo[]
}
