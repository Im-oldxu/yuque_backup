import { describe, expect, it } from 'vitest'
import { formatBytes, formatDateTime, formatDuration, percent } from './format'

describe('display formatters', () => {
  it('formats byte values with stable binary units', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1024)).toBe('1.0 KB')
    expect(formatBytes(1073741824)).toBe('1.0 GB')
  })

  it('rounds retention seconds up to visible days', () => {
    expect(formatDuration(1)).toBe('1 天')
    expect(formatDuration(86401)).toBe('2 天')
    expect(formatDuration(null)).toBe('不适用')
  })

  it('clamps progress to the documented percentage range', () => {
    expect(percent(-1)).toBe('0%')
    expect(percent(62.4)).toBe('62%')
    expect(percent(120)).toBe('100%')
  })

  it('formats scheduled runs in the configured IANA timezone', () => {
    const value = '2026-01-01T00:00:00Z'
    expect(formatDateTime(value, 'UTC')).toContain('00:00')
    expect(formatDateTime(value, 'Asia/Shanghai')).toContain('08:00')
    expect(formatDateTime(value, 'Invalid/Timezone')).toContain('08:00')
  })
})
