import { API_BASE, API_MODE, createIdempotencyKey, httpRequest, httpTextRequest, type RequestOptions } from './client'
import { mockRequest } from './mock'
import type {
  Admin, AssetListParams, AssetReference, BackupIssue, BackupJob, BackupSubtask, Credential, CredentialListParams,
  DashboardSummary, DocumentDetail, DocumentListParams, DocumentSummary, InitializationStatus, IssueListParams,
  JobIssueListParams, JobListParams, JobScope, Operation, Paginated, PaginationParams, Repository,
  QuotaEstimate, RepositoryListParams, RetentionSetting, ScheduleSetting, SearchResults, StorageSetting, SubtaskListParams,
  TocTree, Tombstone, TombstoneListParams, VersionDetail, VersionListParams, VersionSummary,
} from './types'

async function request<T>(path: string, options?: RequestOptions): Promise<T> {
  return API_MODE === 'mock' ? mockRequest<T>(path, options) : httpRequest<T>(path, options)
}

type QueryPrimitive = string | number | boolean | null | undefined

function query(values: Record<string, QueryPrimitive | readonly QueryPrimitive[]>): string {
  const params = new URLSearchParams()
  Object.entries(values).forEach(([key, value]) => {
    const entries = Array.isArray(value) ? value : [value]
    entries.forEach((entry) => {
      if (entry !== null && entry !== undefined && entry !== '') params.append(key, String(entry))
    })
  })
  const result = params.toString()
  return result ? `?${result}` : ''
}

function listQuery<T extends PaginationParams>(filters: T): string {
  return query({ ...filters, page: filters.page ?? 1, page_size: filters.page_size ?? 20 } as unknown as Record<string, QueryPrimitive | readonly QueryPrimitive[]>)
}

export const api = {
  getInitialization: () => request<InitializationStatus>('/system/initialization'),
  initialize: (username: string, password: string) => request<Admin>('/system/initialize', { method: 'POST', csrf: false, idempotencyKey: createIdempotencyKey(), body: { username, password } }),
  login: (username: string, password: string) => request<Admin>('/auth/login', { method: 'POST', csrf: false, body: { username, password } }),
  getMe: () => request<Admin>('/auth/me'),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  updatePassword: (current_password: string, new_password: string) => request<void>('/auth/password', { method: 'PUT', body: { current_password, new_password } }),

  getDashboard: () => request<DashboardSummary>('/dashboard/summary'),
  getCredentials: (filters: CredentialListParams = {}) => request<Paginated<Credential>>(`/credentials${listQuery(filters)}`),
  createCredential: (body: { name: string; base_url: string; token: string }) => request<{ credential: Credential; operation: Operation }>('/credentials', { method: 'POST', body }),
  getCredential: (id: string) => request<Credential>(`/credentials/${id}`),
  updateCredential: (id: string, body: Partial<{ name: string; base_url: string; token: string }>) => request<Credential>(`/credentials/${id}`, { method: 'PATCH', body }),
  verifyCredential: (id: string) => request<Operation>(`/credentials/${id}/verify`, { method: 'POST' }),
  discoverRepositories: (id: string) => request<Operation>(`/credentials/${id}/discover-repositories`, { method: 'POST' }),
  enableCredential: (id: string) => request<Credential>(`/credentials/${id}/enable`, { method: 'POST' }),
  disableCredential: (id: string) => request<Credential>(`/credentials/${id}/disable`, { method: 'POST' }),
  deleteCredential: (id: string) => request<void>(`/credentials/${id}`, { method: 'DELETE' }),
  getOperation: (id: string) => request<Operation>(`/operations/${id}`),

  getRepositories: (filters: RepositoryListParams = {}) => request<Paginated<Repository>>(`/repositories${listQuery(filters)}`),
  getRepository: (id: string) => request<Repository>(`/repositories/${id}`),
  updateRepositorySelection: (id: string, selected: boolean) => request<Repository>(`/repositories/${id}/selection`, { method: 'PATCH', body: { selected } }),
  setPrimaryCredential: (id: string, credential_id: string) => request<Repository>(`/repositories/${id}/primary-credential`, { method: 'PUT', body: { credential_id } }),
  getToc: (id: string) => request<TocTree>(`/repositories/${id}/toc`),
  getDocuments: (filters: DocumentListParams = {}) => request<Paginated<DocumentSummary>>(`/documents${listQuery(filters)}`),
  search: (q: string, filters: { repository_id?: string; deleted?: boolean } = {}) => request<SearchResults>(`/search${query({ q, ...filters })}`),
  getDocument: (id: string) => request<DocumentDetail>(`/documents/${id}`),
  getVersions: (id: string, filters: VersionListParams = {}) => request<Paginated<VersionSummary>>(`/documents/${id}/versions${listQuery(filters)}`),
  getVersion: (documentId: string, versionId: string) => request<VersionDetail>(`/documents/${documentId}/versions/${versionId}`),
  getVersionAssets: (documentId: string, versionId: string, filters: AssetListParams = {}) => request<Paginated<AssetReference>>(`/documents/${documentId}/versions/${versionId}/assets${listQuery(filters)}`),
  getVersionIssues: (documentId: string, versionId: string, filters: IssueListParams = {}) => request<Paginated<BackupIssue>>(`/documents/${documentId}/versions/${versionId}/issues${listQuery(filters)}`),
  getPreviewHtml: (documentId: string, versionId: string) => API_MODE === 'mock' ? fetch('/mock-preview.html').then((response) => response.text()) : httpTextRequest(`/documents/${documentId}/versions/${versionId}/preview`),
  getMarkdown: (documentId: string, versionId: string) => API_MODE === 'mock'
    ? Promise.resolve('# 备份与恢复操作手册\n\n> 变更前先确认备份完整。\n\n## 执行步骤\n\n| 阶段 | 检查项 |\n| --- | --- |\n| 备份 | 校验版本与附件 |\n| 恢复 | 验证服务状态 |\n\n### 命令示例\n\n```bash\nyuque-backup export\n```\n\n## 验收结果\n\n正文、表格与代码块均可阅读。')
    : httpTextRequest(`/documents/${documentId}/versions/${versionId}/markdown`),

  getJobs: (filters: JobListParams = {}) => request<Paginated<BackupJob>>(`/backup-jobs${listQuery(filters)}`),
  getJob: (id: string) => request<BackupJob>(`/backup-jobs/${id}`),
  estimateJob: (scope: JobScope) => request<QuotaEstimate>('/backup-jobs/estimate', { method: 'POST', body: { scope } }),
  createJob: (scope: JobScope) => request<{ job: BackupJob; merged: boolean }>('/backup-jobs', { method: 'POST', idempotencyKey: createIdempotencyKey(), body: { scope } }),
  cancelJob: (id: string) => request<BackupJob>(`/backup-jobs/${id}/cancel`, { method: 'POST' }),
  rerunJob: (id: string) => request<{ job: BackupJob; merged: boolean }>(`/backup-jobs/${id}/rerun`, { method: 'POST', idempotencyKey: createIdempotencyKey() }),
  getSubtasks: (id: string, filters: SubtaskListParams = {}) => request<Paginated<BackupSubtask>>(`/backup-jobs/${id}/subtasks${listQuery(filters)}`),
  getJobIssues: (id: string, filters: JobIssueListParams = {}) => request<Paginated<BackupIssue>>(`/backup-jobs/${id}/issues${listQuery(filters)}`),

  getSchedule: () => request<ScheduleSetting>('/settings/schedule'),
  updateSchedule: (cron: string, timezone: string) => request<ScheduleSetting>('/settings/schedule', { method: 'PUT', body: { cron, timezone } }),
  getRetention: () => request<RetentionSetting>('/settings/retention'),
  updateRetention: (retention_days: number) => request<RetentionSetting>('/settings/retention', { method: 'PUT', body: { retention_days } }),
  getStorage: () => request<StorageSetting>('/settings/storage'),
  updateStorageLimit: (max_asset_size_bytes: number | null) => request<StorageSetting>('/settings/storage-limit', { method: 'PUT', body: { max_asset_size_bytes } }),
  getTombstones: (filters: TombstoneListParams | string = {}) => {
    const normalized = typeof filters === 'string' ? { q: filters } : filters
    return request<Paginated<Tombstone>>(`/deletion-tombstones${listQuery(normalized)}`)
  },
  getTombstone: (id: string) => request<Tombstone>(`/deletion-tombstones/${id}`),

  previewUrl: (documentId: string, versionId: string) => API_MODE === 'mock' ? '/mock-preview.html' : `${API_BASE}/documents/${documentId}/versions/${versionId}/preview`,
  downloadUrl: (documentId: string, versionId: string, kind: 'raw-response' | 'raw-body' | 'markdown' | 'offline-html' | 'pdf') => API_MODE === 'mock' ? `data:text/plain;charset=utf-8,${encodeURIComponent(`Yuque Backup Mock: ${kind}`)}` : `${API_BASE}/documents/${documentId}/versions/${versionId}/downloads/${kind}`,
  assetDownloadUrl: (assetId: string) => API_MODE === 'mock' ? `data:text/plain;charset=utf-8,${encodeURIComponent(`Mock asset: ${assetId}`)}` : `${API_BASE}/assets/${assetId}/download`,
}

export * from './client'
export * from './types'
