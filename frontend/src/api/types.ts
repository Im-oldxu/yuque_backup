export type JobStatus = 'queued' | 'running' | 'waiting_quota' | 'succeeded' | 'partial' | 'failed' | 'cancelled'
export type CredentialStatus = 'unverified' | 'valid' | 'waiting_quota' | 'action_required' | 'disabled'
export type Completeness = 'complete' | 'partial' | 'failed'
export type OperationStatus = 'queued' | 'running' | 'waiting_quota' | 'succeeded' | 'failed' | 'cancelled'
export type DocumentType = 'Doc' | 'Sheet' | 'Thread' | 'Board' | 'Table' | 'HtmlDoc' | 'unknown'

export interface Paginated<T> { items: T[]; page: number; page_size: number; total: number }
export interface PaginationParams { page?: number; page_size?: number }
export interface ApiErrorBody {
  code: string
  message: string
  request_id: string
  field_errors?: Array<{ field: string; reason: string }>
  retry_after_seconds?: number
}
export interface InitializationStatus { initialized: boolean }
export interface Admin { id: string; username: string; created_at: string; password_changed_at: string | null }
export interface RateLimitSnapshot { limit: number; remaining: number; observed_at: string }

export interface Operation {
  id: string
  type: 'credential_verify' | 'repository_discovery'
  status: OperationStatus
  credential_id: string
  result: Record<string, unknown> | null
  error: { code: string; message: string } | null
  next_retry_at: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface Credential {
  id: string
  name: string
  base_url: string
  token_masked: string
  subject_type: 'user' | 'group' | 'unknown'
  subject_id: string | null
  login: string | null
  status: CredentialStatus
  enabled: boolean
  last_verified_at: string | null
  rate_limit: RateLimitSnapshot | null
  next_retry_at: string | null
  active_operation_id: string | null
  repository_count: number
  created_at: string
  updated_at: string
}

export interface RepositoryCredentialSummary { id: string; name: string; status: CredentialStatus; enabled: boolean }
export interface Repository {
  id: string
  yuque_book_id: string
  base_url: string
  name: string
  slug: string | null
  namespace: string | null
  selected: boolean
  connection_status: 'connected' | 'disabled' | 'action_required'
  primary_credential_id: string | null
  credential_count: number
  document_count: number
  last_success_at: string | null
  content_updated_at: string | null
  credentials?: RepositoryCredentialSummary[]
}

export interface TocNode { id: string; type: string; title: string; document_id: string | null; path: string; children: TocNode[] }
export interface TocTree { repository_id: string; updated_at: string; items: TocNode[] }

export interface DocumentSummary {
  id: string
  repository_id: string
  yuque_doc_id: string
  type: DocumentType
  title: string
  slug: string | null
  path: string
  deleted_at: string | null
  purge_at: string | null
  latest_version_id: string | null
  latest_version_completeness: Completeness | null
  updated_at: string
}

export interface VersionSummary {
  id: string
  remote_version_id: string | null
  format: string | null
  content_hash: string
  completeness: Completeness
  is_latest: boolean
  preview_available: boolean
  resource_total: number
  resource_downloaded: number
  issue_count: number
  source_job_id: string
  remote_updated_at: string | null
  created_at: string
}

export interface DocumentDetail extends DocumentSummary {
  repository: Pick<Repository, 'id' | 'name' | 'namespace'>
  original_path: string
  remaining_retention_seconds: number | null
  latest_successful_version: VersionSummary | null
  version_count: number
}

export interface VersionDetail extends VersionSummary {
  document_id: string
  downloads: { raw_response: boolean; raw_body: boolean; markdown: boolean; offline_html: boolean; pdf: boolean }
  asset_summary: { total: number; downloaded: number; failed: number; skipped: number }
}

export interface AssetReference {
  id: string
  asset_id: string | null
  name: string
  type: string
  mime_type: string | null
  size: number | null
  status: 'pending' | 'downloaded' | 'skipped' | 'failed'
  inline_available: boolean
  download_available: boolean
  issue_code: string | null
}

export interface BackupIssue {
  id: string
  level: 'warning' | 'error'
  code: string
  message: string
  credential_id: string | null
  repository_id: string | null
  document_id: string | null
  document_title: string | null
  asset_id: string | null
  asset_type: string | null
  safe_url: string | null
  http_status: number | null
  attempt_count: number
  first_occurred_at: string
  last_occurred_at: string
}

export type JobScope =
  | { type: 'all' }
  | { type: 'credential'; credential_id: string }
  | { type: 'repository'; repository_id: string }
  | { type: 'repositories'; credential_id: string; repository_ids: string[] }

export interface QuotaEstimateCredential {
  credential_id: string
  credential_name: string
  repository_count: number
  document_count: number
  estimated_api_calls: number
  rate_limit_limit: number | null
  rate_limit_remaining: number | null
  rate_limit_observed_at: string | null
  snapshot_fresh: boolean
  sufficient: boolean | null
}

export interface QuotaEstimate {
  repository_count: number
  document_count: number
  estimated_api_calls: number
  is_precise: false
  credentials: QuotaEstimateCredential[]
  calculation_basis: string[]
}

export interface CredentialListParams extends PaginationParams {
  status?: CredentialStatus
  enabled?: boolean
}

export interface RepositoryListParams extends PaginationParams {
  q?: string
  selected?: boolean
  connection_status?: Repository['connection_status']
  credential_id?: string
}

export interface DocumentListParams extends PaginationParams {
  q?: string
  repository_id?: string
  toc_item_id?: string
  deleted?: boolean
  completeness?: Completeness
}

export type VersionListParams = PaginationParams

export interface AssetListParams extends PaginationParams {
  status?: AssetReference['status']
  type?: string
}

export interface IssueListParams extends PaginationParams {
  level?: BackupIssue['level']
  code?: string
}

export interface JobListParams extends PaginationParams {
  status?: JobStatus | JobStatus[]
  trigger?: BackupJob['trigger']
  credential_id?: string
  repository_id?: string
  created_from?: string
  created_to?: string
}

export interface SubtaskListParams extends PaginationParams {
  status?: JobStatus
  credential_id?: string
  repository_id?: string
}

export interface JobIssueListParams extends PaginationParams {
  level?: BackupIssue['level']
  credential_id?: string
  repository_id?: string
  document_id?: string
  asset_id?: string
  code?: string
}

export interface TombstoneListParams extends PaginationParams {
  q?: string
  repository_id?: string
  deleted_from?: string
  deleted_to?: string
}

export interface SearchResults {
  repositories: Repository[]
  documents: DocumentSummary[]
}

export interface BackupJob {
  id: string
  trigger: 'manual' | 'cron'
  scope: JobScope
  status: JobStatus
  progress: number
  document_total: number
  document_succeeded: number
  document_partial: number
  document_failed: number
  asset_total: number
  asset_succeeded: number
  asset_failed: number
  issue_count: number
  waiting_quota_credentials: number
  next_retry_at: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  cancel_requested_at: string | null
  can_cancel: boolean
  can_rerun: boolean
}

export interface BackupActivity {
  stage: 'queued' | 'waiting_retry' | 'repository_metadata' | 'repository_toc' | 'repository_documents' | 'repository_deletions' | 'document_fetch' | 'resource_download' | 'resource_retry' | 'document_commit'
  document_title: string | null
  resource_name: string | null
  resource_completed: number
  resource_total: number
  attempt: number | null
  max_attempts: number | null
  retry_in_seconds: number | null
  last_error_code: string | null
  updated_at: string | null
}

export interface BackupSubtask {
  id: string
  credential: Pick<Credential, 'id' | 'name' | 'status'>
  repository: Pick<Repository, 'id' | 'name'>
  status: JobStatus
  document_total: number
  document_completed: number
  issue_count: number
  next_retry_at: string | null
  last_issue: string | null
  activity: BackupActivity | null
  created_at: string
}

export interface DashboardSummary {
  schedule: { enabled: boolean; cron: string; timezone: string; next_run_at: string | null }
  current_job: BackupJob | null
  last_success_at: string | null
  waiting_quota_credentials: number
  job_counts: { succeeded: number; partial: number; failed: number }
  repositories: number
  documents: number
  versions: number
  storage: { database_bytes: number; content_bytes: number; asset_bytes: number; total_bytes: number }
  worker: { status: 'online' | 'offline'; last_heartbeat_at: string | null }
}

export interface ScheduleSetting { cron: string; timezone: string; next_runs: string[]; updated_at: string }
export interface RetentionSetting { retention_days: number; updated_at: string }
export interface StorageSetting {
  database_path: string
  content_path: string
  max_asset_size_bytes: number | null
  max_asset_size_unlimited: boolean
  usage: { database_bytes: number; version_bytes: number; asset_bytes: number; total_bytes: number }
  updated_at: string
}

export interface Tombstone {
  id: string
  base_url: string
  yuque_book_id: string
  yuque_doc_id: string
  title: string
  original_path: string
  repository: Pick<Repository, 'id' | 'name'>
  deleted_at: string
  purged_at: string
  source_job_id: string
  cleanup_job_id: string
}
