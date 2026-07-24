export function formatDateTime(value: string | null | undefined, timeZone = 'Asia/Shanghai'): string {
  if (!value) return '暂无'
  try {
    return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short', timeZone }).format(new Date(value))
  } catch {
    return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Shanghai' }).format(new Date(value))
  }
}

export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return '未知'
  if (value === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return '不适用'
  return `${Math.max(0, Math.ceil(seconds / 86400))} 天`
}

export function percent(value: number): string {
  return `${Math.max(0, Math.min(100, Math.round(value)))}%`
}
