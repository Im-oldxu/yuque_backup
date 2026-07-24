import { ApiError, notifyAuthRequired, type RequestOptions } from './client'
import type {
  Admin,
  AssetReference,
  BackupIssue,
  BackupJob,
  BackupSubtask,
  Credential,
  DashboardSummary,
  DocumentSummary,
  JobScope,
  Operation,
  Paginated,
  Repository,
  RetentionSetting,
  ScheduleSetting,
  StorageSetting,
  Tombstone,
  VersionSummary,
} from './types'

const now = '2026-07-23T14:30:00Z'
const yesterday = '2026-07-22T18:10:00Z'
const delayMs = Number(import.meta.env.VITE_MOCK_DELAY ?? 250)
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const credentialStatusValues = ['unverified', 'valid', 'waiting_quota', 'action_required', 'disabled'] as const
const connectionStatusValues = ['connected', 'disabled', 'action_required'] as const
const completenessValues = ['complete', 'partial', 'failed'] as const
const jobStatusValues = ['queued', 'running', 'waiting_quota', 'succeeded', 'partial', 'failed', 'cancelled'] as const
const jobTriggerValues = ['manual', 'cron'] as const
const assetStatusValues = ['pending', 'downloaded', 'skipped', 'failed'] as const
const issueLevelValues = ['warning', 'error'] as const

function fixtureUuid(namespace: number, sequence: number): string {
  return `${namespace.toString(16).padStart(8, '0')}-0000-4000-8000-${sequence.toString(16).padStart(12, '0')}`
}

const fixtureIds = {
  admin: fixtureUuid(1, 1),
  credentials: [fixtureUuid(2, 1), fixtureUuid(2, 2)],
  repositories: [fixtureUuid(3, 1), fixtureUuid(3, 2), fixtureUuid(3, 3)],
  documents: Array.from({ length: 6 }, (_, index) => fixtureUuid(4, index + 1)),
  currentVersions: Array.from({ length: 6 }, (_, index) => fixtureUuid(5, index + 1)),
  previousVersions: Array.from({ length: 6 }, (_, index) => fixtureUuid(6, index + 1)),
  issue: fixtureUuid(7, 1),
  jobs: [fixtureUuid(8, 1), fixtureUuid(8, 2), fixtureUuid(8, 3)],
  tombstone: fixtureUuid(9, 1),
  cleanupJob: fixtureUuid(10, 1),
  assets: [fixtureUuid(12, 1), fixtureUuid(12, 2)],
  tocRoots: Array.from({ length: 3 }, (_, index) => fixtureUuid(13, index + 1)),
  tocDocuments: Array.from({ length: 6 }, (_, index) => fixtureUuid(14, index + 1)),
} as const

interface MockAdminRecord {
  version: 1
  username: string
  password_salt: string
  password_verifier: string
  created_at: string
  password_changed_at: string | null
  session_version: number
}

const adminRecordKey = 'yb_mock_admin_v1'
const sessionVersionKey = 'yb_mock_session_version'

function readAdminRecord(): MockAdminRecord | null {
  try {
    const value = JSON.parse(localStorage.getItem(adminRecordKey) ?? 'null') as Partial<MockAdminRecord> | null
    return value?.version === 1 && typeof value.username === 'string' && typeof value.password_salt === 'string'
      && typeof value.password_verifier === 'string' && typeof value.created_at === 'string'
      && typeof value.session_version === 'number' ? value as MockAdminRecord : null
  } catch {
    return null
  }
}

let adminRecord = readAdminRecord()
let initialized = adminRecord !== null
let authenticated = initialized && Number(sessionStorage.getItem(sessionVersionKey)) === adminRecord?.session_version

if (!initialized) {
  localStorage.removeItem('yb_mock_initialized')
  localStorage.removeItem('yb_mock_admin')
  sessionStorage.removeItem('yb_mock_authenticated')
  sessionStorage.removeItem(sessionVersionKey)
}

const admin = (): Admin => {
  if (!adminRecord) fail(401, 'AUTH_REQUIRED', '登录状态已失效，请重新登录。')
  return { id: fixtureIds.admin, username: adminRecord.username, created_at: adminRecord.created_at, password_changed_at: adminRecord.password_changed_at }
}

const credentials: Credential[] = [
  {
    id: fixtureIds.credentials[0], name: '个人语雀', base_url: 'https://www.yuque.com', token_masked: '************Ab3x',
    subject_type: 'user', subject_id: '12345', login: 'lin-demo', status: 'valid', enabled: true,
    last_verified_at: now, rate_limit: { limit: 5000, remaining: 4932, observed_at: now }, next_retry_at: null,
    active_operation_id: null, repository_count: 2, created_at: '2026-07-20T08:00:00Z', updated_at: now,
  },
  {
    id: fixtureIds.credentials[1], name: '产品空间', base_url: 'https://acme.yuque.com', token_masked: '************F91q',
    subject_type: 'group', subject_id: '67890', login: 'acme', status: 'waiting_quota', enabled: true,
    last_verified_at: yesterday, rate_limit: { limit: 5000, remaining: 0, observed_at: now }, next_retry_at: '2026-07-23T15:00:00Z',
    active_operation_id: null, repository_count: 2, created_at: '2026-07-20T09:00:00Z', updated_at: now,
  },
]

const repositories: Repository[] = [
  { id: fixtureIds.repositories[0], yuque_book_id: '10001', base_url: 'https://www.yuque.com', name: '产品知识库', slug: 'product', namespace: 'lin-demo/product', selected: true, connection_status: 'connected', primary_credential_id: fixtureIds.credentials[0], credential_count: 1, document_count: 4, last_success_at: yesterday, content_updated_at: now },
  { id: fixtureIds.repositories[1], yuque_book_id: '10002', base_url: 'https://www.yuque.com', name: '研发手册', slug: 'engineering', namespace: 'lin-demo/engineering', selected: true, connection_status: 'action_required', primary_credential_id: null, credential_count: 2, document_count: 2, last_success_at: yesterday, content_updated_at: yesterday },
  { id: fixtureIds.repositories[2], yuque_book_id: '10003', base_url: 'https://acme.yuque.com', name: '历史归档', slug: 'archive', namespace: 'acme/archive', selected: false, connection_status: 'connected', primary_credential_id: fixtureIds.credentials[1], credential_count: 1, document_count: 0, last_success_at: '2026-07-10T18:00:00Z', content_updated_at: '2026-07-08T05:00:00Z' },
]

const repositoryCredentialIds = new Map<string, string[]>([
  [fixtureIds.repositories[0], [fixtureIds.credentials[0]]],
  [fixtureIds.repositories[1], [fixtureIds.credentials[0], fixtureIds.credentials[1]]],
  [fixtureIds.repositories[2], [fixtureIds.credentials[1]]],
])

const docTypes: DocumentSummary['type'][] = ['Doc', 'HtmlDoc', 'Sheet', 'Table', 'Thread', 'Board']
const docTitles = ['备份与恢复操作手册', '部署检查清单', '资源台账', '版本审计数据表', '故障处理讨论', '产品架构图集']
const documents: DocumentSummary[] = docTypes.map((type, index) => ({
  id: fixtureIds.documents[index]!, repository_id: index < 4 ? fixtureIds.repositories[0] : fixtureIds.repositories[1], yuque_doc_id: String(20001 + index),
  type, title: docTitles[index]!, slug: `doc-${index + 1}`, path: `/运维/${docTitles[index]}`,
  deleted_at: index === 5 ? '2026-07-20T10:00:00Z' : null, purge_at: index === 5 ? '2026-08-04T10:00:00Z' : null,
  latest_version_id: fixtureIds.currentVersions[index]!, latest_version_completeness: index === 2 ? 'partial' : 'complete', updated_at: now,
}))

function versionsFor(documentId: string): VersionSummary[] {
  const index = documents.findIndex((item) => item.id === documentId) + 1
  return [
    { id: fixtureIds.currentVersions[index - 1]!, remote_version_id: `remote-${index}-3`, format: documents[index - 1]?.type === 'Sheet' ? 'lakesheet' : 'markdown', content_hash: `sha256:${index}current`, completeness: index === 3 ? 'partial' : 'complete', is_latest: true, preview_available: true, resource_total: 3, resource_downloaded: index === 3 ? 2 : 3, issue_count: index === 3 ? 1 : 0, source_job_id: fixtureIds.jobs[1], remote_updated_at: now, created_at: now },
    { id: fixtureIds.previousVersions[index - 1]!, remote_version_id: `remote-${index}-2`, format: 'markdown', content_hash: `sha256:${index}previous`, completeness: 'complete', is_latest: false, preview_available: true, resource_total: 2, resource_downloaded: 2, issue_count: 0, source_job_id: fixtureIds.jobs[2], remote_updated_at: yesterday, created_at: yesterday },
  ]
}

const issues: BackupIssue[] = [
  { id: fixtureIds.issue, level: 'warning', code: 'ASSET_DOWNLOAD_FAILED', message: '一项图片资源在重试后仍无法下载，正文已经安全保存。', credential_id: fixtureIds.credentials[0], repository_id: fixtureIds.repositories[0], document_id: fixtureIds.documents[2]!, document_title: '资源台账', asset_id: null, asset_type: 'image', safe_url: 'https://cdn.example.com/resource.png', http_status: 403, attempt_count: 3, first_occurred_at: now, last_occurred_at: now },
]

const jobs: BackupJob[] = [
  { id: fixtureIds.jobs[0], trigger: 'manual', scope: { type: 'all' }, status: 'running', progress: 62, document_total: 6, document_succeeded: 3, document_partial: 1, document_failed: 0, asset_total: 12, asset_succeeded: 7, asset_failed: 1, issue_count: 1, waiting_quota_credentials: 1, next_retry_at: '2026-07-23T15:00:00Z', created_at: now, started_at: now, finished_at: null, cancel_requested_at: null, can_cancel: true, can_rerun: false },
  { id: fixtureIds.jobs[1], trigger: 'cron', scope: { type: 'all' }, status: 'partial', progress: 100, document_total: 6, document_succeeded: 5, document_partial: 1, document_failed: 0, asset_total: 12, asset_succeeded: 11, asset_failed: 1, issue_count: 1, waiting_quota_credentials: 0, next_retry_at: null, created_at: yesterday, started_at: yesterday, finished_at: '2026-07-22T18:20:00Z', cancel_requested_at: null, can_cancel: false, can_rerun: true },
  { id: fixtureIds.jobs[2], trigger: 'cron', scope: { type: 'all' }, status: 'succeeded', progress: 100, document_total: 6, document_succeeded: 6, document_partial: 0, document_failed: 0, asset_total: 12, asset_succeeded: 12, asset_failed: 0, issue_count: 0, waiting_quota_credentials: 0, next_retry_at: null, created_at: '2026-07-21T18:00:00Z', started_at: '2026-07-21T18:00:00Z', finished_at: '2026-07-21T18:09:00Z', cancel_requested_at: null, can_cancel: false, can_rerun: false },
]

const schedule: ScheduleSetting = { cron: '0 2 * * *', timezone: 'Asia/Shanghai', next_runs: ['2026-07-24T18:00:00Z', '2026-07-25T18:00:00Z', '2026-07-26T18:00:00Z'], updated_at: now }
const retention: RetentionSetting = { retention_days: 15, updated_at: now }
const storage: StorageSetting = { database_path: '/data/db', content_path: '/data/content', max_asset_size_bytes: 524288000, max_asset_size_unlimited: false, usage: { database_bytes: 10485760, version_bytes: 340787200, asset_bytes: 734003200, total_bytes: 1085276160 }, updated_at: now }
const tombstones: Tombstone[] = [{ id: fixtureIds.tombstone, base_url: 'https://www.yuque.com', yuque_book_id: '10001', yuque_doc_id: '19001', title: '旧版上线清单', original_path: '/历史/旧版上线清单', repository: { id: fixtureIds.repositories[0], name: '产品知识库' }, deleted_at: '2026-06-01T06:00:00Z', purged_at: '2026-06-16T06:10:00Z', source_job_id: fixtureIds.jobs[2], cleanup_job_id: fixtureIds.cleanupJob }]

const operations = new Map<string, { operation: Operation; polls: number }>()
const idempotencyRecords = new Map<string, { fingerprint: string; response: unknown }>()
const generatedLocalIds = new Map<string, string>()

function clone<T>(value: T): T { return structuredClone(value) }
function stableGeneratedUuid(key: string): string {
  const existing = generatedLocalIds.get(key)
  if (existing) return existing
  const id = crypto.randomUUID()
  generatedLocalIds.set(key, id)
  return id
}

function credentialCanConnect(credential: Credential | undefined): boolean {
  return Boolean(credential?.enabled && ['valid', 'waiting_quota'].includes(credential.status))
}

function syncRepositoryConnection(repository: Repository): void {
  const accessibleIds = repositoryCredentialIds.get(repository.id) ?? []
  repository.credential_count = accessibleIds.length
  const primary = credentials.find((credential) => credential.id === repository.primary_credential_id)
  if (credentialCanConnect(primary)) {
    repository.connection_status = 'connected'
    return
  }
  const hasAlternative = accessibleIds.some((credentialId) => credentialId !== repository.primary_credential_id
    && credentialCanConnect(credentials.find((credential) => credential.id === credentialId)))
  repository.connection_status = hasAlternative ? 'action_required' : 'disabled'
}

function syncCredentialRepositoryCount(credential: Credential): void {
  credential.repository_count = Array.from(repositoryCredentialIds.values()).filter((ids) => ids.includes(credential.id)).length
}

function syncRepositoriesForCredential(credentialId: string): void {
  repositories.filter((repository) => repositoryCredentialIds.get(repository.id)?.includes(credentialId)
    || repository.primary_credential_id === credentialId).forEach(syncRepositoryConnection)
}

function applyRepositoryDiscovery(credential: Credential): Record<string, number> {
  const matchingRepositories = repositories.filter((repository) => repository.base_url === credential.base_url)
  matchingRepositories.forEach((repository) => {
    const accessibleIds = repositoryCredentialIds.get(repository.id) ?? []
    if (!accessibleIds.includes(credential.id)) accessibleIds.push(credential.id)
    repositoryCredentialIds.set(repository.id, accessibleIds)
    syncRepositoryConnection(repository)
  })
  syncCredentialRepositoryCount(credential)
  return {
    discovered: matchingRepositories.length,
    created: 0,
    updated: matchingRepositories.length,
    duplicates: matchingRepositories.length,
    requires_primary_selection: matchingRepositories.filter((repository) => repository.primary_credential_id === null).length,
  }
}

function removeCredentialRelations(credentialId: string): void {
  repositories.forEach((repository) => {
    const remainingIds = (repositoryCredentialIds.get(repository.id) ?? []).filter((id) => id !== credentialId)
    repositoryCredentialIds.set(repository.id, remainingIds)
    if (repository.primary_credential_id === credentialId) repository.primary_credential_id = null
    syncRepositoryConnection(repository)
  })
}
function page<T>(items: T[], pageNumber = 1, pageSize = 20): Paginated<T> { return { items: clone(items.slice((pageNumber - 1) * pageSize, pageNumber * pageSize)), page: pageNumber, page_size: pageSize, total: items.length } }
function fail(status: number, code: string, message: string, fieldErrors?: Array<{ field: string; reason: string }>): never {
  throw new ApiError(status, { code, message, request_id: `mock_${crypto.randomUUID()}`, field_errors: fieldErrors })
}
function requireAuth() {
  authenticated = adminRecord !== null && Number(sessionStorage.getItem(sessionVersionKey)) === adminRecord.session_version
  if (!authenticated) {
    notifyAuthRequired()
    fail(401, 'AUTH_REQUIRED', '登录状态已失效，请重新登录。')
  }
}
async function wait() { await new Promise((resolve) => setTimeout(resolve, Math.max(0, delayMs))) }

function persistAdminRecord(record: MockAdminRecord): void {
  adminRecord = record
  initialized = true
  localStorage.setItem(adminRecordKey, JSON.stringify(record))
  localStorage.setItem('yb_mock_initialized', 'true')
  localStorage.setItem('yb_mock_admin', record.username)
}

function openSession(): void {
  if (!adminRecord) return
  authenticated = true
  sessionStorage.setItem(sessionVersionKey, String(adminRecord.session_version))
  sessionStorage.setItem('yb_mock_authenticated', 'true')
}

function closeSession(): void {
  authenticated = false
  sessionStorage.removeItem(sessionVersionKey)
  sessionStorage.removeItem('yb_mock_authenticated')
}

function randomSalt(): string {
  const value = crypto.getRandomValues(new Uint8Array(16))
  return Array.from(value, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

async function passwordVerifier(password: string, salt: string): Promise<string> {
  const encoder = new TextEncoder()
  const key = await crypto.subtle.importKey('raw', encoder.encode(password), 'PBKDF2', false, ['deriveBits'])
  const bits = await crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt: encoder.encode(salt), iterations: 100_000 }, key, 256)
  return Array.from(new Uint8Array(bits), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

function sameVerifier(left: string, right: string): boolean {
  if (left.length !== right.length) return false
  let difference = 0
  for (let index = 0; index < left.length; index += 1) difference |= left.charCodeAt(index) ^ right.charCodeAt(index)
  return difference === 0
}

function validateAdminInput(username: unknown, password: unknown): { username: string; password: string } {
  const normalizedUsername = typeof username === 'string' ? username.trim() : ''
  const rawPassword = typeof password === 'string' ? password : ''
  const fieldErrors: Array<{ field: string; reason: string }> = []
  if (normalizedUsername.length < 3) fieldErrors.push({ field: 'username', reason: 'min_length' })
  else if (normalizedUsername.length > 64) fieldErrors.push({ field: 'username', reason: 'max_length' })
  if (rawPassword.length < 12) fieldErrors.push({ field: 'password', reason: 'min_length' })
  else if (rawPassword.length > 128) fieldErrors.push({ field: 'password', reason: 'max_length' })
  if (fieldErrors.length) fail(422, 'VALIDATION_ERROR', '请求参数不合法。', fieldErrors)
  return { username: normalizedUsername, password: rawPassword }
}

function validateNewPassword(password: unknown): string {
  const rawPassword = typeof password === 'string' ? password : ''
  if (rawPassword.length < 12) fail(422, 'VALIDATION_ERROR', '请求参数不合法。', [{ field: 'new_password', reason: 'min_length' }])
  if (rawPassword.length > 128) fail(422, 'VALIDATION_ERROR', '请求参数不合法。', [{ field: 'new_password', reason: 'max_length' }])
  return rawPassword
}

function requireExactFields(body: Record<string, unknown>, fields: string[]): void {
  const unknown = Object.keys(body).filter((field) => !fields.includes(field))
  const missing = fields.filter((field) => !Object.prototype.hasOwnProperty.call(body, field))
  const fieldErrors = [
    ...unknown.map((field) => ({ field, reason: 'extra_forbidden' })),
    ...missing.map((field) => ({ field, reason: 'required' })),
  ]
  if (fieldErrors.length) fail(422, 'VALIDATION_ERROR', '请求字段不合法。', fieldErrors)
}

function requirePatchFields(body: Record<string, unknown>, fields: string[]): void {
  const provided = Object.keys(body)
  const unknown = provided.filter((field) => !fields.includes(field))
  const fieldErrors = unknown.map((field) => ({ field, reason: 'extra_forbidden' }))
  if (!provided.length) fieldErrors.push({ field: 'body', reason: 'at_least_one_field' })
  if (fieldErrors.length) fail(422, 'VALIDATION_ERROR', '请求字段不合法。', fieldErrors)
}

function requireUuid(value: unknown, field: string): string {
  if (typeof value !== 'string' || !uuidPattern.test(value)) fail(422, 'VALIDATION_ERROR', '本地资源 ID 必须是 UUID。', [{ field, reason: 'invalid_uuid' }])
  return value
}

function idempotency(path: string, options: RequestOptions, body: unknown): { key: string; fingerprint: string; replay?: unknown } {
  const key = options.idempotencyKey
  if (!key) fail(400, 'IDEMPOTENCY_KEY_REQUIRED', '缺少 Idempotency-Key。')
  if (!uuidPattern.test(key)) {
    fail(422, 'VALIDATION_ERROR', 'Idempotency-Key 必须是 UUID。', [{ field: 'Idempotency-Key', reason: 'invalid_uuid' }])
  }
  const fingerprint = JSON.stringify(canonicalValue(body ?? null))
  const record = idempotencyRecords.get(`${path}:${key}`)
  if (record && record.fingerprint !== fingerprint) fail(409, 'IDEMPOTENCY_CONFLICT', '同一 Idempotency-Key 不能用于不同请求。')
  return { key, fingerprint, replay: record ? clone(record.response) : undefined }
}

function canonicalValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalValue)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(Object.entries(value as Record<string, unknown>)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, entry]) => [key, canonicalValue(entry)]))
}

function rememberIdempotency(path: string, state: { key: string; fingerprint: string }, response: unknown): void {
  idempotencyRecords.set(`${path}:${state.key}`, { fingerprint: state.fingerprint, response: clone(response) })
}

function pagination(url: URL): { pageNumber: number; pageSize: number } {
  const pageNumber = Number(url.searchParams.get('page') ?? 1)
  const pageSize = Number(url.searchParams.get('page_size') ?? 20)
  if (!Number.isInteger(pageNumber) || pageNumber < 1) fail(422, 'VALIDATION_ERROR', '分页参数不合法。', [{ field: 'page', reason: 'greater_than_equal' }])
  if (!Number.isInteger(pageSize) || pageSize < 1 || pageSize > 100) fail(422, 'VALIDATION_ERROR', '分页参数不合法。', [{ field: 'page_size', reason: 'range' }])
  return { pageNumber, pageSize }
}

function requireQueryFields(url: URL, fields: string[]): void {
  const unknown = new Set<string>()
  url.searchParams.forEach((_value, field) => { if (!fields.includes(field)) unknown.add(field) })
  if (unknown.size) fail(422, 'VALIDATION_ERROR', '查询参数不合法。', Array.from(unknown, (field) => ({ field, reason: 'extra_forbidden' })))
}

function queryBoolean(url: URL, field: string): boolean | undefined {
  const value = url.searchParams.get(field)
  if (value === null) return undefined
  if (value !== 'true' && value !== 'false') fail(422, 'VALIDATION_ERROR', '布尔筛选值不合法。', [{ field, reason: 'boolean' }])
  return value === 'true'
}

function queryEnum<T extends string>(url: URL, field: string, values: readonly T[]): T | undefined {
  const value = url.searchParams.get(field)
  if (value === null) return undefined
  if (!values.includes(value as T)) fail(422, 'VALIDATION_ERROR', '枚举筛选值不合法。', [{ field, reason: 'enum' }])
  return value as T
}

function queryEnums<T extends string>(url: URL, field: string, values: readonly T[]): T[] {
  const selected = url.searchParams.getAll(field)
  if (selected.some((value) => !values.includes(value as T))) fail(422, 'VALIDATION_ERROR', '枚举筛选值不合法。', [{ field, reason: 'enum' }])
  return selected as T[]
}

function queryUuid(url: URL, field: string): string | undefined {
  const value = url.searchParams.get(field)
  return value === null ? undefined : requireUuid(value, field)
}

function queryIsoDate(url: URL, field: string): string | undefined {
  const value = url.searchParams.get(field)
  if (value === null) return undefined
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|[+-](\d{2}):(\d{2}))$/.exec(value)
  const [year, month, day, hour, minute, second, offsetHour, offsetMinute] = match
    ? match.slice(1).map((part) => part === undefined ? undefined : Number(part))
    : []
  const leapYear = year !== undefined && (year % 4 === 0 && year % 100 !== 0 || year % 400 === 0)
  const daysInMonth = month === 2 ? (leapYear ? 29 : 28) : [4, 6, 9, 11].includes(month ?? 0) ? 30 : 31
  const valid = match !== null && year !== undefined && year >= 1
    && month !== undefined && month >= 1 && month <= 12
    && day !== undefined && day >= 1 && day <= daysInMonth
    && hour !== undefined && hour <= 23
    && minute !== undefined && minute <= 59
    && second !== undefined && second <= 59
    && (offsetHour === undefined || offsetHour <= 23)
    && (offsetMinute === undefined || offsetMinute <= 59)
    && !Number.isNaN(Date.parse(value))
  if (!valid) fail(422, 'VALIDATION_ERROR', '时间筛选值必须是带时区的 ISO 8601。', [{ field, reason: 'datetime' }])
  return value
}

interface CronField {
  values: Set<number>
  wildcard: boolean
}

interface ParsedCron {
  minute: CronField
  hour: CronField
  dayOfMonth: CronField
  month: CronField
  dayOfWeek: CronField
}

const monthAliases: Record<string, number> = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12 }
const weekdayAliases: Record<string, number> = { sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 }

function cronAtom(value: string, minimum: number, maximum: number, aliases: Record<string, number>): number {
  const normalized = value.toLowerCase()
  const parsed = aliases[normalized] ?? (/^\d+$/.test(normalized) ? Number(normalized) : Number.NaN)
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) throw new Error('invalid cron atom')
  return parsed
}

function parseCronField(source: string, minimum: number, maximum: number, aliases: Record<string, number> = {}, normalize: (value: number) => number = (value) => value): CronField {
  if (!source) throw new Error('empty cron field')
  const values = new Set<number>()
  source.split(',').forEach((segment) => {
    const stepParts = segment.split('/')
    if (stepParts.length > 2 || !stepParts[0]) throw new Error('invalid cron step')
    const step = stepParts[1] === undefined ? 1 : Number(stepParts[1])
    if (!Number.isInteger(step) || step < 1) throw new Error('invalid cron step')
    const rangeSource = stepParts[0]
    let start: number
    let end: number
    if (rangeSource === '*') {
      start = minimum
      end = maximum
    } else {
      const rangeParts = rangeSource.split('-')
      if (rangeParts.length > 2 || !rangeParts[0]) throw new Error('invalid cron range')
      start = cronAtom(rangeParts[0], minimum, maximum, aliases)
      end = rangeParts[1] === undefined
        ? (stepParts[1] === undefined ? start : maximum)
        : cronAtom(rangeParts[1], minimum, maximum, aliases)
      if (start > end) throw new Error('invalid cron range')
    }
    for (let value = start; value <= end; value += step) values.add(normalize(value))
  })
  if (!values.size) throw new Error('empty cron values')
  return { values, wildcard: source === '*' }
}

function parseCron(expression: string): ParsedCron {
  const fields = expression.trim().split(/\s+/)
  if (fields.length !== 5) throw new Error('cron must have five fields')
  return {
    minute: parseCronField(fields[0]!, 0, 59),
    hour: parseCronField(fields[1]!, 0, 23),
    dayOfMonth: parseCronField(fields[2]!, 1, 31),
    month: parseCronField(fields[3]!, 1, 12, monthAliases),
    dayOfWeek: parseCronField(fields[4]!, 0, 7, weekdayAliases, (value) => value === 7 ? 0 : value),
  }
}

interface LocalDateTimeParts {
  year: number
  month: number
  day: number
  hour: number
  minute: number
}

const dateTimeFormatters = new Map<string, Intl.DateTimeFormat>()

function localDateTimeParts(date: Date, timezone: string): LocalDateTimeParts {
  let formatter = dateTimeFormatters.get(timezone)
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('en-CA-u-hc-h23', {
      timeZone: timezone,
      calendar: 'gregory',
      numberingSystem: 'latn',
      year: 'numeric',
      month: 'numeric',
      day: 'numeric',
      hour: 'numeric',
      minute: 'numeric',
      hourCycle: 'h23',
    })
    dateTimeFormatters.set(timezone, formatter)
  }
  const parts = Object.fromEntries(formatter.formatToParts(date).map((part) => [part.type, part.value]))
  return { year: Number(parts.year), month: Number(parts.month), day: Number(parts.day), hour: Number(parts.hour), minute: Number(parts.minute) }
}

function localDateTimeToUtc(parts: LocalDateTimeParts, timezone: string): Date | null {
  const expected = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute)
  let timestamp = expected
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const actual = localDateTimeParts(new Date(timestamp), timezone)
    const difference = expected - Date.UTC(actual.year, actual.month - 1, actual.day, actual.hour, actual.minute)
    if (difference === 0) break
    timestamp += difference
  }
  const candidate = new Date(timestamp)
  const actual = localDateTimeParts(candidate, timezone)
  return actual.year === parts.year && actual.month === parts.month && actual.day === parts.day
    && actual.hour === parts.hour && actual.minute === parts.minute ? candidate : null
}

function cronDayMatches(cron: ParsedCron, dayOfMonth: number, dayOfWeek: number): boolean {
  const matchesMonthDay = cron.dayOfMonth.values.has(dayOfMonth)
  const matchesWeekday = cron.dayOfWeek.values.has(dayOfWeek)
  if (cron.dayOfMonth.wildcard) return cron.dayOfWeek.wildcard || matchesWeekday
  if (cron.dayOfWeek.wildcard) return matchesMonthDay
  return matchesMonthDay || matchesWeekday
}

function nextCronRuns(cron: ParsedCron, timezone: string, after: Date, count = 3): string[] {
  const start = localDateTimeParts(after, timezone)
  const calendarDay = new Date(Date.UTC(start.year, start.month - 1, start.day))
  const hours = Array.from(cron.hour.values).sort((left, right) => left - right)
  const minutes = Array.from(cron.minute.values).sort((left, right) => left - right)
  const runs: Date[] = []

  for (let dayOffset = 0; dayOffset < 366 * 8; dayOffset += 1) {
    const year = calendarDay.getUTCFullYear()
    const month = calendarDay.getUTCMonth() + 1
    const day = calendarDay.getUTCDate()
    if (cron.month.values.has(month) && cronDayMatches(cron, day, calendarDay.getUTCDay())) {
      hours.forEach((hour) => minutes.forEach((minute) => {
        const candidate = localDateTimeToUtc({ year, month, day, hour, minute }, timezone)
        if (candidate && candidate.getTime() > after.getTime()) runs.push(candidate)
      }))
    }
    runs.sort((left, right) => left.getTime() - right.getTime())
    if (runs.length >= count) return runs.slice(0, count).map((date) => date.toISOString())
    calendarDay.setUTCDate(calendarDay.getUTCDate() + 1)
  }
  fail(422, 'INVALID_CRON', 'Cron 表达式无法产生后续运行时间。')
}

function validateTimezone(value: unknown): string {
  const timezone = typeof value === 'string' ? value.trim() : ''
  try {
    if (!timezone) throw new Error('empty timezone')
    new Intl.DateTimeFormat('en-US', { timeZone: timezone }).format(new Date())
    return timezone
  } catch {
    fail(422, 'INVALID_TIMEZONE', '时区必须是有效的 IANA 名称。')
  }
}

function sameScope(left: JobScope, right: JobScope): boolean {
  if (left.type !== right.type) return false
  if (left.type === 'credential' && right.type === 'credential') return left.credential_id === right.credential_id
  if (left.type === 'repository' && right.type === 'repository') return left.repository_id === right.repository_id
  return left.type === 'all' && right.type === 'all'
}

function mergeScope(left: JobScope, right: JobScope): JobScope {
  if (sameScope(left, right)) return clone(left)
  if (left.type === 'all' || right.type === 'all') return { type: 'all' }
  if (left.type === 'credential' && right.type === 'repository') {
    const repository = repositories.find((item) => item.id === right.repository_id)
    if (repository?.primary_credential_id === left.credential_id) return clone(left)
  }
  if (left.type === 'repository' && right.type === 'credential') return mergeScope(right, left)
  return { type: 'all' }
}

function newQueuedJob(scope: JobScope): BackupJob {
  return {
    id: crypto.randomUUID(), trigger: 'manual', scope: clone(scope), status: 'queued', progress: 0,
    document_total: 0, document_succeeded: 0, document_partial: 0, document_failed: 0,
    asset_total: 0, asset_succeeded: 0, asset_failed: 0, issue_count: 0,
    waiting_quota_credentials: 0, next_retry_at: null, created_at: new Date().toISOString(),
    started_at: null, finished_at: null, cancel_requested_at: null, can_cancel: true, can_rerun: false,
  }
}

function enqueueOrMerge(scope: JobScope): { job: BackupJob; merged: boolean } {
  const queued = jobs.find((item) => item.status === 'queued' && item.cancel_requested_at === null)
  if (queued) {
    queued.scope = mergeScope(queued.scope, scope)
    return { job: clone(queued), merged: true }
  }
  const job = newQueuedJob(scope)
  jobs.unshift(job)
  return { job: clone(job), merged: false }
}

function validateJobScope(value: unknown): JobScope {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(422, 'VALIDATION_ERROR', '任务范围不合法。', [{ field: 'scope', reason: 'object_type' }])
  const scope = value as Record<string, unknown>
  if (scope.type === 'all') {
    requireExactFields(scope, ['type'])
    const available = repositories.some((repository) => {
      const credential = credentials.find((item) => item.id === repository.primary_credential_id)
      return repository.selected && credential?.enabled
    })
    if (!available) fail(409, 'NO_ENABLED_TARGETS', '没有可备份的已启用目标。')
    return { type: 'all' }
  }
  if (scope.type === 'credential') {
    requireExactFields(scope, ['type', 'credential_id'])
    const credentialId = requireUuid(scope.credential_id, 'scope.credential_id')
    const credential = credentials.find((item) => item.id === credentialId)
    if (!credential) fail(404, 'CREDENTIAL_NOT_FOUND', '凭据不存在。')
    const available = credential.enabled && repositories.some((repository) => repository.selected && repository.primary_credential_id === credential.id)
    if (!available) fail(409, 'NO_ENABLED_TARGETS', '该凭据没有可备份的已启用目标。')
    return { type: 'credential', credential_id: credential.id }
  }
  if (scope.type === 'repository') {
    requireExactFields(scope, ['type', 'repository_id'])
    const repositoryId = requireUuid(scope.repository_id, 'scope.repository_id')
    const repository = repositories.find((item) => item.id === repositoryId)
    if (!repository) fail(404, 'REPOSITORY_NOT_FOUND', '知识库不存在。')
    if (!repository.primary_credential_id) fail(409, 'PRIMARY_CREDENTIAL_REQUIRED', '请先为知识库指定主凭据。')
    const credential = credentials.find((item) => item.id === repository.primary_credential_id)
    if (!credential?.enabled) fail(409, 'NO_ENABLED_TARGETS', '知识库的主凭据当前不可用。')
    return { type: 'repository', repository_id: repository.id }
  }
  fail(422, 'VALIDATION_ERROR', '任务范围不合法。', [{ field: 'scope.type', reason: 'enum' }])
}

function scopeIncludesCredential(scope: JobScope, credentialId: string): boolean {
  if (scope.type === 'all') return true
  if (scope.type === 'credential') return scope.credential_id === credentialId
  return repositories.find((item) => item.id === scope.repository_id)?.primary_credential_id === credentialId
}

function scopeIncludesRepository(scope: JobScope, repositoryId: string): boolean {
  if (scope.type === 'all') return true
  if (scope.type === 'repository') return scope.repository_id === repositoryId
  return repositories.find((item) => item.id === repositoryId)?.primary_credential_id === scope.credential_id
}

function createOperation(type: Operation['type'], credentialId: string): Operation {
  const operation: Operation = { id: crypto.randomUUID(), type, status: 'queued', credential_id: credentialId, result: null, error: null, next_retry_at: null, created_at: new Date().toISOString(), started_at: null, finished_at: null }
  operations.set(operation.id, { operation, polls: 0 })
  const credential = credentials.find((item) => item.id === credentialId)
  if (credential) credential.active_operation_id = operation.id
  return clone(operation)
}

function dashboard(): DashboardSummary {
  const current = jobs.find((item) => ['running', 'waiting_quota'].includes(item.status))
    ?? jobs.find((item) => item.status === 'queued')
    ?? null
  return {
    schedule: { enabled: true, cron: schedule.cron, timezone: schedule.timezone, next_run_at: schedule.next_runs[0] ?? null },
    current_job: clone(current),
    last_success_at: yesterday,
    waiting_quota_credentials: credentials.filter((item) => item.status === 'waiting_quota').length,
    job_counts: {
      succeeded: jobs.filter((item) => item.status === 'succeeded').length,
      partial: jobs.filter((item) => item.status === 'partial').length,
      failed: jobs.filter((item) => item.status === 'failed').length,
    },
    repositories: repositories.filter((item) => item.selected).length,
    documents: documents.length,
    versions: documents.reduce((total, document) => total + versionsFor(document.id).length, 0),
    storage: { database_bytes: storage.usage.database_bytes, content_bytes: storage.usage.version_bytes + storage.usage.asset_bytes, asset_bytes: storage.usage.asset_bytes, total_bytes: storage.usage.total_bytes },
    worker: { status: 'online', last_heartbeat_at: now },
  }
}

export async function mockRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  await wait()
  adminRecord = readAdminRecord()
  initialized = adminRecord !== null
  if (import.meta.env.VITE_MOCK_FORCE_ERROR === 'true' && path !== '/system/initialization') fail(503, 'SERVICE_UNAVAILABLE', 'Mock 已配置为服务不可用状态。')
  const method = options.method ?? 'GET'
  const url = new URL(path, 'http://mock.local')
  const body = (options.body ?? {}) as Record<string, any>
  const parts = url.pathname.split('/').filter(Boolean)

  if (method === 'GET' && url.pathname === '/system/initialization') { requireQueryFields(url, []); return clone({ initialized }) as T }
  if (method === 'POST' && url.pathname === '/system/initialize') {
    requireExactFields(body, ['username', 'password'])
    const idempotent = idempotency(url.pathname, options, body)
    if (idempotent.replay !== undefined) return idempotent.replay as T
    if (initialized) fail(409, 'INITIALIZATION_ALREADY_COMPLETED', '系统已经完成初始化。')
    const input = validateAdminInput(body.username, body.password)
    const salt = randomSalt()
    persistAdminRecord({
      version: 1,
      username: input.username,
      password_salt: salt,
      password_verifier: await passwordVerifier(input.password, salt),
      created_at: new Date().toISOString(),
      password_changed_at: null,
      session_version: 1,
    })
    openSession()
    const response = admin()
    rememberIdempotency(url.pathname, idempotent, response)
    return clone(response) as T
  }
  if (method === 'POST' && url.pathname === '/auth/login') {
    requireExactFields(body, ['username', 'password'])
    const input = validateAdminInput(body.username, body.password)
    const verifier = adminRecord ? await passwordVerifier(input.password, adminRecord.password_salt) : ''
    if (!adminRecord || input.username !== adminRecord.username || !sameVerifier(verifier, adminRecord.password_verifier)) {
      fail(401, 'INVALID_CREDENTIALS', '用户名或密码不正确。')
    }
    openSession()
    return clone(admin()) as T
  }
  if (method === 'GET' && url.pathname === '/auth/me') { requireQueryFields(url, []); requireAuth(); return clone(admin()) as T }
  if (method === 'POST' && url.pathname === '/auth/logout') { requireExactFields(body, []); requireAuth(); closeSession(); return undefined as T }
  if (method === 'PUT' && url.pathname === '/auth/password') {
    requireExactFields(body, ['current_password', 'new_password'])
    requireAuth()
    const newPassword = validateNewPassword(body.new_password)
    if (typeof body.current_password !== 'string') fail(422, 'VALIDATION_ERROR', '请求参数不合法。', [{ field: 'current_password', reason: 'string_type' }])
    const currentPassword = body.current_password
    const currentVerifier = await passwordVerifier(currentPassword, adminRecord!.password_salt)
    if (!sameVerifier(currentVerifier, adminRecord!.password_verifier)) fail(400, 'CURRENT_PASSWORD_INCORRECT', '当前密码不正确。')
    const salt = randomSalt()
    persistAdminRecord({
      ...adminRecord!,
      password_salt: salt,
      password_verifier: await passwordVerifier(newPassword, salt),
      password_changed_at: new Date().toISOString(),
      session_version: adminRecord!.session_version + 1,
    })
    openSession()
    return undefined as T
  }

  requireAuth()
  if (method === 'GET' && url.pathname === '/dashboard/summary') { requireQueryFields(url, []); return clone(dashboard()) as T }
  if (method === 'GET' && url.pathname === '/credentials') {
    requireQueryFields(url, ['page', 'page_size', 'status', 'enabled'])
    const { pageNumber, pageSize } = pagination(url)
    const status = queryEnum(url, 'status', credentialStatusValues)
    const enabled = queryBoolean(url, 'enabled')
    const filtered = credentials.filter((credential) => (!status || credential.status === status) && (enabled === undefined || credential.enabled === enabled))
      .sort((left, right) => left.created_at.localeCompare(right.created_at) || left.id.localeCompare(right.id))
    return page(filtered, pageNumber, pageSize) as T
  }
  if (method === 'POST' && url.pathname === '/credentials') {
    requireExactFields(body, ['name', 'base_url', 'token'])
    const name = typeof body.name === 'string' ? body.name.trim() : ''
    const token = typeof body.token === 'string' ? body.token : ''
    let baseUrl: URL
    try { baseUrl = new URL(String(body.base_url)) } catch { fail(422, 'INVALID_BASE_URL', '基础域名必须是有效的 HTTPS origin。') }
    if (!name || name.length > 100) fail(422, 'VALIDATION_ERROR', '请求参数不合法。', [{ field: 'name', reason: name ? 'max_length' : 'min_length' }])
    if (credentials.some((item) => item.name === name)) fail(409, 'CREDENTIAL_NAME_EXISTS', '凭据名称已经存在。')
    if (!token || token.length > 2048) fail(422, 'VALIDATION_ERROR', '请求参数不合法。', [{ field: 'token', reason: token ? 'max_length' : 'min_length' }])
    if (baseUrl!.protocol !== 'https:' || baseUrl!.pathname !== '/' || baseUrl!.search || baseUrl!.hash || baseUrl!.username || baseUrl!.password) {
      fail(422, 'INVALID_BASE_URL', '基础域名必须是 HTTPS origin。')
    }
    const credential: Credential = { id: crypto.randomUUID(), name, base_url: baseUrl!.origin, token_masked: `************${token.slice(-4)}`, subject_type: 'unknown', subject_id: null, login: null, status: 'unverified', enabled: false, last_verified_at: null, rate_limit: null, next_retry_at: null, active_operation_id: null, repository_count: 0, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }
    credentials.push(credential); const operation = createOperation('credential_verify', credential.id)
    return clone({ credential, operation }) as T
  }
  if (parts[0] === 'operations' && method === 'GET') {
    requireQueryFields(url, [])
    const state = operations.get(parts[1] ?? '')
    if (!state) fail(404, 'OPERATION_NOT_FOUND', '操作不存在。')
    state.polls += 1
    if (state.polls === 1) { state.operation.status = 'running'; state.operation.started_at = new Date().toISOString() }
    if (state.polls >= 2) {
      state.operation.status = 'succeeded'; state.operation.finished_at = new Date().toISOString()
      const credential = credentials.find((item) => item.id === state.operation.credential_id)
      if (credential && state.operation.type === 'credential_verify') {
        state.operation.result = { subject_type: 'user', login: 'mock-user' }
        credential.status = 'valid'; credential.subject_type = 'user'; credential.subject_id = 'mock-user-id'; credential.login = 'mock-user'; credential.last_verified_at = new Date().toISOString()
        syncRepositoriesForCredential(credential.id)
      } else if (credential) {
        state.operation.result = applyRepositoryDiscovery(credential)
      }
      if (credential) credential.active_operation_id = null
    }
    return clone(state.operation) as T
  }
  if (parts[0] === 'credentials' && parts[1]) {
    const credential = credentials.find((item) => item.id === parts[1])
    if (!credential) fail(404, 'CREDENTIAL_NOT_FOUND', '凭据不存在。')
    if (parts.length === 2 && method === 'GET') { requireQueryFields(url, []); return clone(credential) as T }
    if (parts.length === 2 && method === 'PATCH') {
      requirePatchFields(body, ['name', 'base_url', 'token'])
      if (body.name !== undefined) {
        const name = typeof body.name === 'string' ? body.name.trim() : ''
        if (!name || name.length > 100) fail(422, 'VALIDATION_ERROR', '请求参数不合法。', [{ field: 'name', reason: name ? 'max_length' : 'min_length' }])
        if (credentials.some((item) => item.id !== credential.id && item.name === name)) fail(409, 'CREDENTIAL_NAME_EXISTS', '凭据名称已经存在。')
        credential.name = name
      }
      if (body.base_url !== undefined) {
        let baseUrl: URL
        try { baseUrl = new URL(String(body.base_url)) } catch { fail(422, 'INVALID_BASE_URL', '基础域名必须是有效的 HTTPS origin。') }
        if (baseUrl!.protocol !== 'https:' || baseUrl!.pathname !== '/' || baseUrl!.search || baseUrl!.hash || baseUrl!.username || baseUrl!.password) fail(422, 'INVALID_BASE_URL', '基础域名必须是 HTTPS origin。')
        credential.base_url = baseUrl!.origin
      }
      if (body.token !== undefined) {
        const token = typeof body.token === 'string' ? body.token : ''
        if (!token || token.length > 2048) fail(422, 'VALIDATION_ERROR', '请求参数不合法。', [{ field: 'token', reason: token ? 'max_length' : 'min_length' }])
        credential.token_masked = `************${token.slice(-4)}`
      }
      if (body.token !== undefined || body.base_url !== undefined) {
        credential.status = 'unverified'
        credential.enabled = false
        syncRepositoriesForCredential(credential.id)
      }
      credential.updated_at = new Date().toISOString()
      return clone(credential) as T
    }
    if (parts.length === 2 && method === 'DELETE') {
      requireExactFields(body, [])
      credentials.splice(credentials.indexOf(credential), 1)
      removeCredentialRelations(credential.id)
      return undefined as T
    }
    if (parts[2] === 'verify' && method === 'POST') {
      requireExactFields(body, [])
      if (credential.active_operation_id) fail(409, 'OPERATION_ALREADY_RUNNING', '该凭据已有进行中的操作。')
      return createOperation('credential_verify', credential.id) as T
    }
    if (parts[2] === 'discover-repositories' && method === 'POST') {
      requireExactFields(body, [])
      if (credential.active_operation_id) fail(409, 'OPERATION_ALREADY_RUNNING', '该凭据已有进行中的操作。')
      if (credential.status !== 'valid' || !credential.enabled) fail(409, 'CREDENTIAL_NOT_VALID', '凭据尚未验证或已停用。')
      return createOperation('repository_discovery', credential.id) as T
    }
    if (parts[2] === 'enable' && method === 'POST') {
      requireExactFields(body, [])
      if (!['valid', 'disabled'].includes(credential.status)) fail(409, 'CREDENTIAL_NOT_VALID', '凭据尚未验证。')
      credential.enabled = true
      credential.status = 'valid'
      syncRepositoriesForCredential(credential.id)
      return clone(credential) as T
    }
    if (parts[2] === 'disable' && method === 'POST') {
      requireExactFields(body, [])
      credential.enabled = false
      credential.status = 'disabled'
      syncRepositoriesForCredential(credential.id)
      return clone(credential) as T
    }
  }

  if (method === 'GET' && url.pathname === '/repositories') {
    requireQueryFields(url, ['page', 'page_size', 'q', 'selected', 'connection_status', 'credential_id'])
    const { pageNumber, pageSize } = pagination(url)
    const query = (url.searchParams.get('q') ?? '').toLowerCase()
    const selected = queryBoolean(url, 'selected')
    const connectionStatus = queryEnum(url, 'connection_status', connectionStatusValues)
    const credentialId = queryUuid(url, 'credential_id')
    const filtered = repositories.filter((item) => (!query || `${item.name} ${item.namespace ?? ''}`.toLowerCase().includes(query))
      && (selected === undefined || item.selected === selected)
      && (!connectionStatus || item.connection_status === connectionStatus)
      && (!credentialId || repositoryCredentialIds.get(item.id)?.includes(credentialId)))
      .sort((left, right) => left.name.localeCompare(right.name, 'zh-CN') || left.id.localeCompare(right.id))
    return page(filtered, pageNumber, pageSize) as T
  }
  if (parts[0] === 'repositories' && parts[1]) {
    const repository = repositories.find((item) => item.id === parts[1])
    if (!repository) fail(404, 'REPOSITORY_NOT_FOUND', '知识库不存在。')
    if (parts.length === 2 && method === 'GET') {
      requireQueryFields(url, [])
      const accessibleIds = repositoryCredentialIds.get(repository.id) ?? []
      return clone({ ...repository, credentials: credentials.filter((item) => accessibleIds.includes(item.id)).map((item) => ({ id: item.id, name: item.name, status: item.status, enabled: item.enabled })) }) as T
    }
    if (parts[2] === 'selection' && method === 'PATCH') {
      requireExactFields(body, ['selected'])
      if (typeof body.selected !== 'boolean') fail(422, 'VALIDATION_ERROR', 'selected 必须是布尔值。', [{ field: 'selected', reason: 'boolean' }])
      repository.selected = body.selected
      return clone(repository) as T
    }
    if (parts[2] === 'primary-credential' && method === 'PUT') {
      requireExactFields(body, ['credential_id'])
      const credentialId = requireUuid(body.credential_id, 'credential_id')
      const credential = credentials.find((item) => item.id === credentialId)
      if (!credential) fail(404, 'CREDENTIAL_NOT_FOUND', '凭据不存在。')
      if (!repositoryCredentialIds.get(repository.id)?.includes(credential.id)) fail(409, 'CREDENTIAL_CANNOT_ACCESS_REPOSITORY', '该凭据不能访问此知识库。')
      repository.primary_credential_id = credential.id
      syncRepositoryConnection(repository)
      return clone(repository) as T
    }
    if (parts[2] === 'toc' && method === 'GET') {
      requireQueryFields(url, [])
      const repoDocs = documents.filter((item) => item.repository_id === repository.id)
      const repositoryIndex = repositories.findIndex((item) => item.id === repository.id)
      return clone({ repository_id: repository.id, updated_at: now, items: [{ id: fixtureIds.tocRoots[repositoryIndex]!, type: 'TITLE', title: '运维与恢复', document_id: null, path: '/运维', children: repoDocs.map((doc) => ({ id: fixtureIds.tocDocuments[documents.findIndex((item) => item.id === doc.id)]!, type: 'DOC', title: doc.title, document_id: doc.id, path: doc.path, children: [] })) }] }) as T
    }
  }

  if (method === 'GET' && url.pathname === '/documents') {
    requireQueryFields(url, ['page', 'page_size', 'q', 'repository_id', 'toc_item_id', 'deleted', 'completeness'])
    const { pageNumber, pageSize } = pagination(url)
    const query = (url.searchParams.get('q') ?? '').toLowerCase()
    const repoId = queryUuid(url, 'repository_id')
    const tocItemId = queryUuid(url, 'toc_item_id')
    const deleted = queryBoolean(url, 'deleted')
    const completeness = queryEnum(url, 'completeness', completenessValues)
    const filtered = documents.filter((item) => (!repoId || item.repository_id === repoId)
      && (!tocItemId || tocItemId === fixtureIds.tocDocuments[documents.findIndex((document) => document.id === item.id)])
      && (!query || `${item.title} ${item.slug ?? ''} ${item.path}`.toLowerCase().includes(query))
      && (deleted === undefined || Boolean(item.deleted_at) === deleted)
      && (!completeness || item.latest_version_completeness === completeness))
      .sort((left, right) => left.title.localeCompare(right.title, 'zh-CN') || left.id.localeCompare(right.id))
    return page(filtered, pageNumber, pageSize) as T
  }
  if (method === 'GET' && url.pathname === '/search') {
    requireQueryFields(url, ['q', 'repository_id', 'deleted'])
    const query = (url.searchParams.get('q') ?? '').trim().toLowerCase()
    if (query.length < 1 || query.length > 200) fail(422, 'VALIDATION_ERROR', '搜索关键词长度不合法。', [{ field: 'q', reason: 'range' }])
    const repoId = queryUuid(url, 'repository_id')
    const deleted = queryBoolean(url, 'deleted')
    return clone({
      repositories: repositories.filter((item) => (!repoId || item.id === repoId) && `${item.name} ${item.namespace ?? ''}`.toLowerCase().includes(query)).slice(0, 20),
      documents: documents.filter((item) => (!repoId || item.repository_id === repoId)
        && (deleted === undefined || Boolean(item.deleted_at) === deleted)
        && `${item.title} ${item.slug ?? ''} ${item.path}`.toLowerCase().includes(query)).slice(0, 20),
    }) as T
  }
  if (parts[0] === 'documents' && parts[1]) {
    const document = documents.find((item) => item.id === parts[1])
    if (!document) fail(404, 'DOCUMENT_NOT_FOUND', '文档不存在。')
    const versions = versionsFor(document.id)
    if (parts.length === 2 && method === 'GET') {
      requireQueryFields(url, [])
      return clone({ ...document, repository: repositories.find((item) => item.id === document.repository_id), original_path: document.path, remaining_retention_seconds: document.deleted_at ? 12 * 86400 : null, latest_successful_version: versions[0], version_count: versions.length }) as T
    }
    if (parts[2] === 'versions' && parts.length === 3 && method === 'GET') {
      requireQueryFields(url, ['page', 'page_size'])
      const { pageNumber, pageSize } = pagination(url)
      const sorted = versions.sort((left, right) => right.created_at.localeCompare(left.created_at) || right.id.localeCompare(left.id))
      return page(sorted, pageNumber, pageSize) as T
    }
    if (parts[2] === 'versions' && parts[3]) {
      const version = versions.find((item) => item.id === parts[3])
      if (!version) fail(404, 'VERSION_NOT_FOUND', '版本不存在。')
      if (parts.length === 4 && method === 'GET') {
        requireQueryFields(url, [])
        return clone({ ...version, document_id: document.id, downloads: { raw_response: true, raw_body: true, offline_html: true }, asset_summary: { total: version.resource_total, downloaded: version.resource_downloaded, failed: version.resource_total - version.resource_downloaded, skipped: 0 } }) as T
      }
      if (parts[4] === 'assets' && method === 'GET') {
        requireQueryFields(url, ['page', 'page_size', 'status', 'type'])
        const documentIndex = documents.findIndex((item) => item.id === document.id) + 1
        const versionOffset = version.is_latest ? 0 : 10
        const assets: AssetReference[] = [{ id: fixtureUuid(11, documentIndex * 100 + versionOffset + 1), asset_id: fixtureIds.assets[0], name: '架构图.png', type: 'image', mime_type: 'image/png', size: 245760, status: 'downloaded', inline_available: true, download_available: true, issue_code: null }, { id: fixtureUuid(11, documentIndex * 100 + versionOffset + 2), asset_id: version.completeness === 'partial' ? null : fixtureIds.assets[1], name: '恢复清单.pdf', type: 'attachment', mime_type: 'application/pdf', size: 1048576, status: version.completeness === 'partial' ? 'failed' : 'downloaded', inline_available: false, download_available: version.completeness !== 'partial', issue_code: version.completeness === 'partial' ? 'ASSET_DOWNLOAD_FAILED' : null }]
        const { pageNumber, pageSize } = pagination(url)
        const status = queryEnum(url, 'status', assetStatusValues)
        const type = url.searchParams.get('type')
        return page(assets.filter((asset) => (!status || asset.status === status) && (!type || asset.type === type)), pageNumber, pageSize) as T
      }
      if (parts[4] === 'issues' && method === 'GET') {
        requireQueryFields(url, ['page', 'page_size', 'level', 'code'])
        const { pageNumber, pageSize } = pagination(url)
        const level = queryEnum(url, 'level', issueLevelValues)
        const code = url.searchParams.get('code')
        const filtered = (version.issue_count ? issues : []).filter((issue) => (!level || issue.level === level) && (!code || issue.code === code))
        return page(filtered, pageNumber, pageSize) as T
      }
    }
  }

  if (method === 'GET' && url.pathname === '/backup-jobs') {
    requireQueryFields(url, ['page', 'page_size', 'status', 'trigger', 'credential_id', 'repository_id', 'created_from', 'created_to'])
    const { pageNumber, pageSize } = pagination(url)
    const statuses = queryEnums(url, 'status', jobStatusValues)
    const trigger = queryEnum(url, 'trigger', jobTriggerValues)
    const credentialId = queryUuid(url, 'credential_id')
    const repositoryId = queryUuid(url, 'repository_id')
    const createdFrom = queryIsoDate(url, 'created_from')
    const createdTo = queryIsoDate(url, 'created_to')
    if (createdFrom && createdTo && Date.parse(createdFrom) > Date.parse(createdTo)) fail(422, 'VALIDATION_ERROR', '创建时间范围不合法。', [{ field: 'created_from', reason: 'range' }])
    const createdFromTime = createdFrom ? Date.parse(createdFrom) : undefined
    const createdToTime = createdTo ? Date.parse(createdTo) : undefined
    const filtered = jobs.filter((job) => (!statuses.length || statuses.includes(job.status))
      && (!trigger || job.trigger === trigger)
      && (!credentialId || scopeIncludesCredential(job.scope, credentialId))
      && (!repositoryId || scopeIncludesRepository(job.scope, repositoryId))
      && (createdFromTime === undefined || Date.parse(job.created_at) >= createdFromTime)
      && (createdToTime === undefined || Date.parse(job.created_at) <= createdToTime))
      .sort((left, right) => right.created_at.localeCompare(left.created_at) || right.id.localeCompare(left.id))
    return page(filtered, pageNumber, pageSize) as T
  }
  if (method === 'POST' && url.pathname === '/backup-jobs') {
    requireExactFields(body, ['scope'])
    const idempotent = idempotency(url.pathname, options, body)
    if (idempotent.replay !== undefined) return idempotent.replay as T
    const response = enqueueOrMerge(validateJobScope(body.scope))
    rememberIdempotency(url.pathname, idempotent, response)
    return clone(response) as T
  }
  if (parts[0] === 'backup-jobs' && parts[1]) {
    const job = jobs.find((item) => item.id === parts[1])
    if (!job) fail(404, 'JOB_NOT_FOUND', '任务不存在。')
    if (parts.length === 2 && method === 'GET') {
      requireQueryFields(url, [])
      return clone(job) as T
    }
    if (parts[2] === 'cancel' && method === 'POST') {
      requireExactFields(body, [])
      if (!['queued', 'running', 'waiting_quota'].includes(job.status) || job.cancel_requested_at) fail(409, 'JOB_NOT_CANCELLABLE', '任务当前不能取消。')
      job.cancel_requested_at = new Date().toISOString()
      job.can_cancel = false
      return clone(job) as T
    }
    if (parts[2] === 'rerun' && method === 'POST') {
      requireExactFields(body, [])
      const idempotent = idempotency(url.pathname, options, null)
      if (idempotent.replay !== undefined) return idempotent.replay as T
      if (!['partial', 'failed', 'cancelled'].includes(job.status)) fail(409, 'JOB_NOT_RERUNNABLE', '任务当前不能重新执行。')
      const response = enqueueOrMerge(job.scope)
      rememberIdempotency(url.pathname, idempotent, response)
      return clone(response) as T
    }
    if (parts[2] === 'subtasks' && method === 'GET') {
      requireQueryFields(url, ['page', 'page_size', 'status', 'credential_id', 'repository_id'])
      const subtasks: BackupSubtask[] = repositories.slice(0, 2).map((repo, index) => ({ id: stableGeneratedUuid(`subtask:${job.id}:${repo.id}`), credential: { id: credentials[index]!.id, name: credentials[index]!.name, status: credentials[index]!.status }, repository: { id: repo.id, name: repo.name }, status: index === 1 && job.status === 'running' ? 'waiting_quota' : job.status, document_total: repo.document_count, document_completed: Math.floor(repo.document_count * job.progress / 100), issue_count: index === 0 ? job.issue_count : 0, next_retry_at: index === 1 ? job.next_retry_at : null, last_issue: index === 0 && job.issue_count ? issues[0]!.message : null, created_at: job.created_at }))
      const { pageNumber, pageSize } = pagination(url)
      const status = queryEnum(url, 'status', jobStatusValues)
      const credentialId = queryUuid(url, 'credential_id')
      const repositoryId = queryUuid(url, 'repository_id')
      return page(subtasks.filter((subtask) => (!status || subtask.status === status)
        && (!credentialId || subtask.credential.id === credentialId)
        && (!repositoryId || subtask.repository.id === repositoryId)), pageNumber, pageSize) as T
    }
    if (parts[2] === 'issues' && method === 'GET') {
      requireQueryFields(url, ['page', 'page_size', 'level', 'credential_id', 'repository_id', 'document_id', 'asset_id', 'code'])
      const { pageNumber, pageSize } = pagination(url)
      const level = queryEnum(url, 'level', issueLevelValues)
      const credentialId = queryUuid(url, 'credential_id')
      const repositoryId = queryUuid(url, 'repository_id')
      const documentId = queryUuid(url, 'document_id')
      const assetId = queryUuid(url, 'asset_id')
      const code = url.searchParams.get('code')
      const filtered = (job.issue_count ? issues : []).filter((issue) => (!level || issue.level === level)
        && (!credentialId || issue.credential_id === credentialId)
        && (!repositoryId || issue.repository_id === repositoryId)
        && (!documentId || issue.document_id === documentId)
        && (!assetId || issue.asset_id === assetId)
        && (!code || issue.code === code))
      return page(filtered, pageNumber, pageSize) as T
    }
  }

  if (method === 'GET' && url.pathname === '/settings/schedule') { requireQueryFields(url, []); return clone(schedule) as T }
  if (method === 'PUT' && url.pathname === '/settings/schedule') {
    requireExactFields(body, ['cron', 'timezone'])
    const cron = typeof body.cron === 'string' ? body.cron.trim() : ''
    let parsed: ParsedCron
    try { parsed = parseCron(cron) } catch { fail(422, 'INVALID_CRON', 'Cron 必须是有效的标准五段表达式。') }
    const timezone = validateTimezone(body.timezone)
    schedule.cron = cron
    schedule.timezone = timezone
    schedule.next_runs = nextCronRuns(parsed!, timezone, new Date())
    schedule.updated_at = new Date().toISOString()
    return clone(schedule) as T
  }
  if (method === 'GET' && url.pathname === '/settings/retention') { requireQueryFields(url, []); return clone(retention) as T }
  if (method === 'PUT' && url.pathname === '/settings/retention') {
    requireExactFields(body, ['retention_days'])
    if (typeof body.retention_days !== 'number' || !Number.isInteger(body.retention_days) || body.retention_days <= 0) fail(422, 'VALIDATION_ERROR', '保留天数必须为正整数。', [{ field: 'retention_days', reason: 'positive_integer' }])
    retention.retention_days = body.retention_days
    retention.updated_at = new Date().toISOString()
    return clone(retention) as T
  }
  if (method === 'GET' && url.pathname === '/settings/storage') { requireQueryFields(url, []); return clone(storage) as T }
  if (method === 'PUT' && url.pathname === '/settings/storage-limit') {
    requireExactFields(body, ['max_asset_size_bytes'])
    const maximum = body.max_asset_size_bytes
    if (maximum !== null && (typeof maximum !== 'number' || !Number.isInteger(maximum) || maximum <= 0)) fail(422, 'VALIDATION_ERROR', '单资源上限必须为正整数或 null。', [{ field: 'max_asset_size_bytes', reason: 'positive_integer_or_null' }])
    storage.max_asset_size_bytes = maximum
    storage.max_asset_size_unlimited = maximum === null
    storage.updated_at = new Date().toISOString()
    return clone(storage) as T
  }
  if (method === 'GET' && url.pathname === '/deletion-tombstones') {
    requireQueryFields(url, ['page', 'page_size', 'q', 'repository_id', 'deleted_from', 'deleted_to'])
    const { pageNumber, pageSize } = pagination(url)
    const query = (url.searchParams.get('q') ?? '').toLowerCase()
    const repositoryId = queryUuid(url, 'repository_id')
    const deletedFrom = queryIsoDate(url, 'deleted_from')
    const deletedTo = queryIsoDate(url, 'deleted_to')
    if (deletedFrom && deletedTo && Date.parse(deletedFrom) > Date.parse(deletedTo)) fail(422, 'VALIDATION_ERROR', '删除时间范围不合法。', [{ field: 'deleted_from', reason: 'range' }])
    const deletedFromTime = deletedFrom ? Date.parse(deletedFrom) : undefined
    const deletedToTime = deletedTo ? Date.parse(deletedTo) : undefined
    const filtered = tombstones.filter((item) => (!query || `${item.title} ${item.original_path}`.toLowerCase().includes(query))
      && (!repositoryId || item.repository.id === repositoryId)
      && (deletedFromTime === undefined || Date.parse(item.deleted_at) >= deletedFromTime)
      && (deletedToTime === undefined || Date.parse(item.deleted_at) <= deletedToTime))
      .sort((left, right) => right.deleted_at.localeCompare(left.deleted_at) || right.id.localeCompare(left.id))
    return page(filtered, pageNumber, pageSize) as T
  }
  if (parts[0] === 'deletion-tombstones' && parts[1] && method === 'GET') { requireQueryFields(url, []); const item = tombstones.find((tombstone) => tombstone.id === parts[1]); if (!item) fail(404, 'TOMBSTONE_NOT_FOUND', '删除记录不存在。'); return clone(item) as T }

  fail(404, 'RESOURCE_NOT_FOUND', `Mock 未找到接口：${method} ${url.pathname}`)
}
