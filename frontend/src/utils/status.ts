import type { Completeness, CredentialStatus, JobStatus, OperationStatus } from '@/api/types'

export const jobStatusText: Record<JobStatus, string> = { queued: '排队中', running: '运行中', waiting_quota: '等待额度', succeeded: '成功', partial: '部分成功', failed: '失败', cancelled: '已取消' }
export const credentialStatusText: Record<CredentialStatus, string> = { unverified: '未验证', valid: '有效', waiting_quota: '等待额度', action_required: '需要处理', disabled: '已停用' }
export const completenessText: Record<Completeness, string> = { complete: '完整', partial: '部分成功', failed: '失败' }
export const operationStatusText: Record<OperationStatus, string> = { queued: '排队中', running: '运行中', waiting_quota: '等待额度', succeeded: '成功', failed: '失败', cancelled: '已取消' }

export function isTerminalJob(status: JobStatus): boolean {
  return ['succeeded', 'partial', 'failed', 'cancelled'].includes(status)
}

export function statusTone(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (['failed', 'action_required'].includes(status)) return 'destructive'
  if (['succeeded', 'valid', 'complete', 'downloaded'].includes(status)) return 'default'
  if (['partial', 'waiting_quota', 'running'].includes(status)) return 'secondary'
  return 'outline'
}
