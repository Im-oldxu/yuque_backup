import type { BackupActivity, BackupIssue } from '@/api'

export function backupActivityText(activity: BackupActivity | null): string {
  if (!activity) return '等待 worker 调度'
  const document = activity.document_title ? `：${activity.document_title}` : ''
  if (activity.stage === 'queued') return `等待 worker 调度${document}`
  if (activity.stage === 'waiting_retry') return `等待重试${document}`
  if (activity.stage === 'repository_metadata') return '正在读取知识库信息'
  if (activity.stage === 'repository_toc') return '正在读取知识库目录'
  if (activity.stage === 'repository_documents') return '正在扫描需要备份的文档'
  if (activity.stage === 'repository_deletions') return '正在检查远端删除记录'
  if (activity.stage === 'document_fetch') return `正在获取文档${document}`
  if (activity.stage === 'document_commit') return `正在保存文档版本${document}`
  const resource = activity.resource_name ? ` · ${activity.resource_name}` : ''
  const progress = activity.resource_total ? `${activity.resource_completed}/${activity.resource_total}` : '准备中'
  const attempt = activity.attempt && activity.max_attempts ? ` · 第 ${activity.attempt}/${activity.max_attempts} 次` : ''
  if (activity.stage === 'resource_retry') {
    const wait = activity.retry_in_seconds ? `，${activity.retry_in_seconds} 秒后重试` : '，等待重试'
    return `资源下载 ${progress}${resource}${attempt}${wait}`
  }
  return `正在下载资源 ${progress}${resource}${attempt}`
}

export function backupIssueMessage(issue: BackupIssue): string {
  if (issue.code === 'RESOURCE_TLS_ERROR') return '资源服务器的 HTTPS 证书无法通过校验，请修复证书链或更新源文档中的资源地址。'
  if (issue.code === 'RESOURCE_NETWORK_ERROR') return '资源下载时发生临时网络错误。'
  return issue.message
}
