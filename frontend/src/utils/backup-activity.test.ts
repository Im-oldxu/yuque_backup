import { describe, expect, it } from 'vitest'

import type { BackupActivity, BackupIssue } from '@/api'
import { backupActivityText, backupIssueMessage } from './backup-activity'

describe('backup activity presentation', () => {
  it('shows the active resource, progress, retry attempt and delay', () => {
    const activity: BackupActivity = {
      stage: 'resource_retry',
      document_title: 'Linux 内核调优',
      resource_name: 'kernel.jpg',
      resource_completed: 1,
      resource_total: 3,
      attempt: 2,
      max_attempts: 4,
      retry_in_seconds: 10,
      last_error_code: 'RESOURCE_NETWORK_ERROR',
      updated_at: '2026-07-24T15:00:00Z',
    }

    expect(backupActivityText(activity)).toBe('资源下载 1/3 · kernel.jpg · 第 2/4 次，10 秒后重试')
  })

  it('explains TLS certificate errors instead of calling them temporary', () => {
    const issue = {
      code: 'RESOURCE_TLS_ERROR',
      message: 'Resource HTTPS certificate verification failed',
    } as BackupIssue

    expect(backupIssueMessage(issue)).toContain('HTTPS 证书无法通过校验')
  })
})
