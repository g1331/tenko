/** 数字、时长与时间的统一展示；缺失值一律显示为 “—”。 */

const EMPTY = '—'
const numberFormat = new Intl.NumberFormat('zh-CN')

export function isNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function numberText(value: unknown): string {
  return isNumber(value) ? numberFormat.format(value) : EMPTY
}

export function durationText(seconds: unknown): string {
  if (!isNumber(seconds)) return EMPTY
  const total = Math.max(Math.floor(seconds), 0)
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  if (days) return `${days}d ${hours}h`
  if (hours) return `${hours}h ${minutes}m`
  if (minutes) return `${minutes}m ${total % 60}s`
  return `${total % 60}s`
}

/** 接口时间为 ISO 8601 UTC；缺少时区标记时按 UTC 处理。 */
export function parseTime(value: string | null | undefined): Date | null {
  if (!value) return null
  const text = String(value)
  const date = new Date(/(?:Z|[+-]\d\d:?\d\d)$/i.test(text) ? text : `${text}Z`)
  return Number.isNaN(date.getTime()) ? null : date
}

/** 本地时间，精确到分钟，例如 2026/10/20 13:05。 */
export function timeText(value: string | null | undefined): string {
  const date = parseTime(value)
  return date
    ? date.toLocaleString('zh-CN', {
        hour12: false,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    : EMPTY
}

export function relativeText(value: string | null | undefined): string {
  const date = parseTime(value)
  if (!date) return EMPTY
  const seconds = Math.round((Date.now() - date.getTime()) / 1000)
  if (seconds < 45) return '刚刚'
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟前`
  if (seconds < 86400) return `${Math.round(seconds / 3600)} 小时前`
  return `${Math.round(seconds / 86400)} 天前`
}

/** 本地时钟，例如 13:05:09。 */
export function clockText(date: Date): string {
  return date.toLocaleTimeString('zh-CN', { hour12: false })
}

export function platformText(value: string | null | undefined): string {
  const known: Record<string, string> = { onebot: 'OneBot 11', satori: 'Satori' }
  const key = String(value ?? '')
  return known[key.toLowerCase()] ?? (key || 'unknown')
}

/** 把 User-Agent 缩成 “Chrome · Linux” 这样的设备描述。 */
export function deviceText(ua: string | null | undefined): string {
  if (!ua) return '未知设备'
  const browser = /Edg\//.test(ua)
    ? 'Edge'
    : /OPR\/|Opera/.test(ua)
      ? 'Opera'
      : /Firefox\//.test(ua)
        ? 'Firefox'
        : /Chrome\/|CriOS/.test(ua)
          ? 'Chrome'
          : /Safari\//.test(ua)
            ? 'Safari'
            : /curl/i.test(ua)
              ? 'curl'
              : '浏览器'
  const os = /Windows/.test(ua)
    ? 'Windows'
    : /Android/.test(ua)
      ? 'Android'
      : /iPhone|iPad|iOS/.test(ua)
        ? 'iOS'
        : /Mac OS X|Macintosh/.test(ua)
          ? 'macOS'
          : /Linux/.test(ua)
            ? 'Linux'
            : ''
  return os ? `${browser} · ${os}` : browser
}
