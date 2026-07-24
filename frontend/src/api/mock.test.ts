import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RequestOptions } from './client'
import type {
  Admin, BackupJob, Credential, DashboardSummary, DocumentSummary, Operation, Paginated,
  Repository, ScheduleSetting, TocTree, Tombstone, VersionSummary,
} from './types'

const password = 'correct-password-123'
const newPassword = 'new-correct-password-456'
const key = (value: number) => `00000000-0000-4000-8000-${String(value).padStart(12, '0')}`
const ids = {
  credentialPersonal: '00000002-0000-4000-8000-000000000001',
  repositoryProduct: '00000003-0000-4000-8000-000000000001',
  repositoryEngineering: '00000003-0000-4000-8000-000000000002',
  partialDocument: '00000004-0000-4000-8000-000000000003',
  partialVersion: '00000005-0000-4000-8000-000000000003',
  runningJob: '00000008-0000-4000-8000-000000000001',
  partialJob: '00000008-0000-4000-8000-000000000002',
  successfulJob: '00000008-0000-4000-8000-000000000003',
  tombstone: '00000009-0000-4000-8000-000000000001',
} as const

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const localIdFields = new Set([
  'id', 'credential_id', 'repository_id', 'document_id', 'asset_id', 'active_operation_id',
  'primary_credential_id', 'latest_version_id', 'source_job_id', 'cleanup_job_id',
])

function collectLocalIds(value: unknown, path = '$', result: Array<{ path: string; value: string }> = []): Array<{ path: string; value: string }> {
  if (Array.isArray(value)) {
    value.forEach((entry, index) => collectLocalIds(entry, `${path}[${index}]`, result))
    return result
  }
  if (!value || typeof value !== 'object') return result
  Object.entries(value as Record<string, unknown>).forEach(([field, entry]) => {
    const fieldPath = `${path}.${field}`
    if (localIdFields.has(field) && typeof entry === 'string') result.push({ path: fieldPath, value: entry })
    collectLocalIds(entry, fieldPath, result)
  })
  return result
}

async function freshMock() {
  vi.resetModules()
  const { mockRequest } = await import('./mock')
  return mockRequest
}

async function initialize(mockRequest: Awaited<ReturnType<typeof freshMock>>) {
  return mockRequest<Admin>('/system/initialize', {
    method: 'POST',
    idempotencyKey: key(1),
    body: { username: 'admin', password },
  })
}

describe('contract-faithful Mock API', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
  })

  afterEach(() => vi.unstubAllGlobals())

  it('stores a password verifier and validates login and password changes', async () => {
    const mockRequest = await freshMock()
    await expect(mockRequest('/system/initialization')).resolves.toEqual({ initialized: false })

    const created = await initialize(mockRequest)
    expect(created.username).toBe('admin')
    expect(created.password_changed_at).toBeNull()
    expect(localStorage.getItem('yb_mock_admin_v1')).not.toContain(password)
    await expect(mockRequest('/auth/me')).resolves.toMatchObject({ username: 'admin' })

    await mockRequest('/auth/logout', { method: 'POST' })
    await expect(mockRequest('/auth/me')).rejects.toMatchObject({ status: 401, code: 'AUTH_REQUIRED' })
    await expect(mockRequest('/auth/login', { method: 'POST', body: { username: 'admin', password: 'another-wrong-password' } }))
      .rejects.toMatchObject({ status: 401, code: 'INVALID_CREDENTIALS' })
    await expect(mockRequest('/auth/login', { method: 'POST', body: { username: 'admin', password } })).resolves.toMatchObject({ username: 'admin' })

    await expect(mockRequest('/auth/password', { method: 'PUT', body: { current_password: 'another-wrong-password', new_password: newPassword } }))
      .rejects.toMatchObject({ status: 400, code: 'CURRENT_PASSWORD_INCORRECT' })
    await expect(mockRequest('/auth/password', { method: 'PUT', body: { current_password: password, new_password: newPassword } })).resolves.toBeUndefined()
    await expect(mockRequest<Admin>('/auth/me')).resolves.toMatchObject({ password_changed_at: expect.any(String) })

    await mockRequest('/auth/logout', { method: 'POST' })
    await expect(mockRequest('/auth/login', { method: 'POST', body: { username: 'admin', password } }))
      .rejects.toMatchObject({ status: 401, code: 'INVALID_CREDENTIALS' })
    await expect(mockRequest('/auth/login', { method: 'POST', body: { username: 'admin', password: newPassword } })).resolves.toMatchObject({ username: 'admin' })
  })

  it('validates initialization fields and replays the original idempotent response', async () => {
    const mockRequest = await freshMock()
    await expect(mockRequest('/system/initialize', { method: 'POST', idempotencyKey: key(2), body: { username: ' a ', password: 'short' } }))
      .rejects.toMatchObject({ status: 422, code: 'VALIDATION_ERROR', fieldErrors: expect.arrayContaining([{ field: 'username', reason: 'min_length' }, { field: 'password', reason: 'min_length' }]) })

    const first = await mockRequest<Admin>('/system/initialize', { method: 'POST', idempotencyKey: key(3), body: { username: ' admin ', password } })
    const replay = await mockRequest<Admin>('/system/initialize', { method: 'POST', idempotencyKey: key(3), body: { username: ' admin ', password } })
    expect(replay).toEqual(first)
    await expect(mockRequest('/system/initialize', { method: 'POST', idempotencyKey: key(3), body: { username: 'admin2', password } }))
      .rejects.toMatchObject({ status: 409, code: 'IDEMPOTENCY_CONFLICT' })
    await expect(mockRequest('/system/initialize', { method: 'POST', idempotencyKey: key(4), body: { username: 'admin2', password } }))
      .rejects.toMatchObject({ status: 409, code: 'INITIALIZATION_ALREADY_COMPLETED' })
  })

  it('rejects missing and unknown fields on reachable write endpoints', async () => {
    const mockRequest = await freshMock()
    const invalidInitializationWrites: Array<{ name: string; path: string; options: RequestOptions }> = [
      { name: 'initialize missing password', path: '/system/initialize', options: { method: 'POST', idempotencyKey: key(40), body: { username: 'admin' } } },
      { name: 'initialize extra field', path: '/system/initialize', options: { method: 'POST', idempotencyKey: key(41), body: { username: 'admin', password, extra: true } } },
    ]
    for (const request of invalidInitializationWrites) {
      await expect(mockRequest(request.path, request.options), request.name)
        .rejects.toMatchObject({ status: 422, code: 'VALIDATION_ERROR' })
    }

    await initialize(mockRequest)
    const invalidWrites: Array<{ name: string; path: string; options: RequestOptions }> = [
      { name: 'login missing password', path: '/auth/login', options: { method: 'POST', body: { username: 'admin' } } },
      { name: 'login extra field', path: '/auth/login', options: { method: 'POST', body: { username: 'admin', password, extra: true } } },
      { name: 'password missing new password', path: '/auth/password', options: { method: 'PUT', body: { current_password: password } } },
      { name: 'password extra field', path: '/auth/password', options: { method: 'PUT', body: { current_password: password, new_password: newPassword, extra: true } } },
      { name: 'credential create extra field', path: '/credentials', options: { method: 'POST', body: { name: '额外字段', base_url: 'https://www.yuque.com', token: 'secret-token', enabled: true } } },
      { name: 'credential patch empty body', path: `/credentials/${ids.credentialPersonal}`, options: { method: 'PATCH', body: {} } },
      { name: 'credential patch unknown field', path: `/credentials/${ids.credentialPersonal}`, options: { method: 'PATCH', body: { enabled: false } } },
      { name: 'credential delete body', path: `/credentials/${ids.credentialPersonal}`, options: { method: 'DELETE', body: { force: true } } },
      { name: 'credential verify body', path: `/credentials/${ids.credentialPersonal}/verify`, options: { method: 'POST', body: { force: true } } },
      { name: 'credential discovery body', path: `/credentials/${ids.credentialPersonal}/discover-repositories`, options: { method: 'POST', body: { force: true } } },
      { name: 'credential enable body', path: `/credentials/${ids.credentialPersonal}/enable`, options: { method: 'POST', body: { force: true } } },
      { name: 'credential disable body', path: `/credentials/${ids.credentialPersonal}/disable`, options: { method: 'POST', body: { force: true } } },
      { name: 'repository selection missing selected', path: `/repositories/${ids.repositoryProduct}/selection`, options: { method: 'PATCH', body: {} } },
      { name: 'repository selection string selected', path: `/repositories/${ids.repositoryProduct}/selection`, options: { method: 'PATCH', body: { selected: 'true' } } },
      { name: 'repository selection extra field', path: `/repositories/${ids.repositoryProduct}/selection`, options: { method: 'PATCH', body: { selected: true, extra: true } } },
      { name: 'primary credential missing ID', path: `/repositories/${ids.repositoryProduct}/primary-credential`, options: { method: 'PUT', body: {} } },
      { name: 'primary credential invalid UUID', path: `/repositories/${ids.repositoryProduct}/primary-credential`, options: { method: 'PUT', body: { credential_id: 'not-a-uuid' } } },
      { name: 'primary credential extra field', path: `/repositories/${ids.repositoryProduct}/primary-credential`, options: { method: 'PUT', body: { credential_id: ids.credentialPersonal, extra: true } } },
      { name: 'job missing scope', path: '/backup-jobs', options: { method: 'POST', idempotencyKey: key(42), body: {} } },
      { name: 'job extra outer field', path: '/backup-jobs', options: { method: 'POST', idempotencyKey: key(43), body: { scope: { type: 'all' }, extra: true } } },
      { name: 'all scope extra field', path: '/backup-jobs', options: { method: 'POST', idempotencyKey: key(44), body: { scope: { type: 'all', credential_id: ids.credentialPersonal } } } },
      { name: 'credential scope missing ID', path: '/backup-jobs', options: { method: 'POST', idempotencyKey: key(45), body: { scope: { type: 'credential' } } } },
      { name: 'credential scope invalid UUID', path: '/backup-jobs', options: { method: 'POST', idempotencyKey: key(46), body: { scope: { type: 'credential', credential_id: 'not-a-uuid' } } } },
      { name: 'repository scope missing ID', path: '/backup-jobs', options: { method: 'POST', idempotencyKey: key(47), body: { scope: { type: 'repository' } } } },
      { name: 'repository scope invalid UUID', path: '/backup-jobs', options: { method: 'POST', idempotencyKey: key(48), body: { scope: { type: 'repository', repository_id: 'not-a-uuid' } } } },
      { name: 'job cancel body', path: `/backup-jobs/${ids.runningJob}/cancel`, options: { method: 'POST', body: { force: true } } },
      { name: 'job rerun body', path: `/backup-jobs/${ids.partialJob}/rerun`, options: { method: 'POST', idempotencyKey: key(49), body: { force: true } } },
      { name: 'logout body', path: '/auth/logout', options: { method: 'POST', body: { everywhere: true } } },
    ]

    for (const request of invalidWrites) {
      await expect(mockRequest(request.path, request.options), request.name)
        .rejects.toMatchObject({ status: 422, code: 'VALIDATION_ERROR' })
    }
  })

  it('rejects invalid pagination, list filters and unknown query fields', async () => {
    const mockRequest = await freshMock()
    await initialize(mockRequest)
    const invalidQueries = [
      '/system/initialization?unknown_filter=value',
      '/auth/me?unknown_filter=value',
      '/dashboard/summary?unknown_filter=value',
      '/credentials?status=unknown',
      '/credentials?enabled=1',
      `/credentials/${ids.credentialPersonal}?unknown_filter=value`,
      '/repositories?selected=1',
      '/repositories?connection_status=unknown',
      '/repositories?credential_id=not-a-uuid',
      '/documents?deleted=1',
      '/documents?completeness=unknown',
      '/documents?repository_id=not-a-uuid',
      '/documents?toc_item_id=not-a-uuid',
      '/documents?page=0',
      '/documents?page_size=101',
      '/documents?unknown_filter=value',
      '/search?q=backup&deleted=1',
      '/search?q=backup&repository_id=not-a-uuid',
      `/documents/${ids.partialDocument}/versions?status=complete`,
      `/documents/${ids.partialDocument}/versions/${ids.partialVersion}/assets?status=unknown`,
      `/documents/${ids.partialDocument}/versions/${ids.partialVersion}/issues?level=info`,
      '/backup-jobs?status=unknown',
      '/backup-jobs?trigger=automatic',
      '/backup-jobs?credential_id=not-a-uuid',
      '/backup-jobs?repository_id=not-a-uuid',
      '/backup-jobs?created_from=not-a-date',
      '/backup-jobs?created_from=2026-07-23T14:30:00',
      '/backup-jobs?created_from=2026-02-30T00:00:00Z',
      '/backup-jobs?created_from=2026-07-24T00:00:00Z&created_to=2026-07-23T00:00:00Z',
      `/backup-jobs/${ids.runningJob}/subtasks?status=unknown`,
      `/backup-jobs/${ids.runningJob}/subtasks?credential_id=not-a-uuid`,
      `/backup-jobs/${ids.partialJob}/issues?level=info`,
      `/backup-jobs/${ids.partialJob}/issues?document_id=not-a-uuid`,
      '/deletion-tombstones?repository_id=not-a-uuid',
      '/deletion-tombstones?deleted_from=not-a-date',
      '/deletion-tombstones?deleted_from=2026-07-23T14:30:00',
      '/deletion-tombstones?deleted_from=2026-07-24T00:00:00Z&deleted_to=2026-07-23T00:00:00Z',
      '/settings/schedule?unknown_filter=value',
      `/deletion-tombstones/${ids.tombstone}?unknown_filter=value`,
    ]

    for (const path of invalidQueries) {
      await expect(mockRequest(path), path)
        .rejects.toMatchObject({ status: 422, code: 'VALIDATION_ERROR' })
    }
  })

  it('merges into one queued task without adding another task when merged is true', async () => {
    const mockRequest = await freshMock()
    await initialize(mockRequest)
    const initial = await mockRequest<Paginated<BackupJob>>('/backup-jobs?page=1&page_size=100')

    const first = await mockRequest<{ job: BackupJob; merged: boolean }>('/backup-jobs', {
      method: 'POST', idempotencyKey: key(10), body: { scope: { type: 'repository', repository_id: ids.repositoryProduct } },
    })
    expect(first.merged).toBe(false)
    expect(first.job.status).toBe('queued')

    const replay = await mockRequest<{ job: BackupJob; merged: boolean }>('/backup-jobs', {
      method: 'POST', idempotencyKey: key(10), body: { scope: { type: 'repository', repository_id: ids.repositoryProduct } },
    })
    expect(replay).toEqual(first)

    const merged = await mockRequest<{ job: BackupJob; merged: boolean }>('/backup-jobs', {
      method: 'POST', idempotencyKey: key(11), body: { scope: { type: 'credential', credential_id: ids.credentialPersonal } },
    })
    expect(merged).toMatchObject({ merged: true, job: { id: first.job.id, scope: { type: 'credential', credential_id: ids.credentialPersonal } } })

    const mergedAll = await mockRequest<{ job: BackupJob; merged: boolean }>('/backup-jobs', {
      method: 'POST', idempotencyKey: key(12), body: { scope: { type: 'all' } },
    })
    const after = await mockRequest<Paginated<BackupJob>>('/backup-jobs?page=1&page_size=100')
    expect(mergedAll).toMatchObject({ merged: true, job: { id: first.job.id, scope: { type: 'all' } } })
    expect(after.total).toBe(initial.total + 1)
    expect(after.items.filter((job) => job.status === 'queued')).toHaveLength(1)

    await expect(mockRequest('/backup-jobs', { method: 'POST', idempotencyKey: key(10), body: { scope: { type: 'all' } } }))
      .rejects.toMatchObject({ status: 409, code: 'IDEMPOTENCY_CONFLICT' })
  })

  it('applies cancellation and rerun state rules and resets a newly queued rerun', async () => {
    const mockRequest = await freshMock()
    await initialize(mockRequest)

    await expect(mockRequest(`/backup-jobs/${ids.successfulJob}/cancel`, { method: 'POST' }))
      .rejects.toMatchObject({ status: 409, code: 'JOB_NOT_CANCELLABLE' })
    const cancelling = await mockRequest<BackupJob>(`/backup-jobs/${ids.runningJob}/cancel`, { method: 'POST' })
    expect(cancelling.status).toBe('running')
    expect(cancelling.cancel_requested_at).toEqual(expect.any(String))
    expect(cancelling.can_cancel).toBe(false)

    await expect(mockRequest(`/backup-jobs/${ids.runningJob}/rerun`, { method: 'POST', idempotencyKey: key(20) }))
      .rejects.toMatchObject({ status: 409, code: 'JOB_NOT_RERUNNABLE' })
    const rerun = await mockRequest<{ job: BackupJob; merged: boolean }>(`/backup-jobs/${ids.partialJob}/rerun`, { method: 'POST', idempotencyKey: key(21) })
    expect(rerun.merged).toBe(false)
    expect(rerun.job).toMatchObject({
      status: 'queued', progress: 0, document_total: 0, asset_total: 0, issue_count: 0,
      cancel_requested_at: null, next_retry_at: null, started_at: null, finished_at: null,
    })
    const merged = await mockRequest<{ job: BackupJob; merged: boolean }>(`/backup-jobs/${ids.partialJob}/rerun`, { method: 'POST', idempotencyKey: key(22) })
    expect(merged).toMatchObject({ merged: true, job: { id: rerun.job.id } })
  })

  it('never returns an updated credential token and honors list pagination', async () => {
    const mockRequest = await freshMock()
    await initialize(mockRequest)

    const updated = await mockRequest<Record<string, unknown>>(`/credentials/${ids.credentialPersonal}`, {
      method: 'PATCH', body: { token: 'replacement-secret-token' },
    })
    expect(updated).not.toHaveProperty('token')
    expect(updated.token_masked).toBe('************oken')
    expect(JSON.stringify(updated)).not.toContain('replacement-secret-token')

    const jobs = await mockRequest<Paginated<BackupJob>>('/backup-jobs?page=2&page_size=1&status=partial&status=succeeded')
    expect(jobs).toMatchObject({ page: 2, page_size: 1, total: 2 })
    expect(jobs.items).toHaveLength(1)
  })

  it('returns UUIDs for every local resource ID and foreign key', async () => {
    const mockRequest = await freshMock()
    const responses: unknown[] = [await initialize(mockRequest)]

    responses.push(await mockRequest('/dashboard/summary'))
    const credentialPage = await mockRequest<Paginated<Credential>>('/credentials?page=1&page_size=100')
    const repositoryPage = await mockRequest<Paginated<Repository>>('/repositories?page=1&page_size=100')
    const documentPage = await mockRequest<Paginated<DocumentSummary>>('/documents?page=1&page_size=100')
    const jobPage = await mockRequest<Paginated<BackupJob>>('/backup-jobs?page=1&page_size=100')
    const tombstonePage = await mockRequest<Paginated<Tombstone>>('/deletion-tombstones?page=1&page_size=100')
    responses.push(credentialPage, repositoryPage, documentPage, jobPage, tombstonePage)

    for (const repository of repositoryPage.items) {
      responses.push(
        await mockRequest(`/repositories/${repository.id}`),
        await mockRequest<TocTree>(`/repositories/${repository.id}/toc`),
      )
    }
    for (const document of documentPage.items) {
      responses.push(await mockRequest(`/documents/${document.id}`))
      const versions = await mockRequest<Paginated<VersionSummary>>(`/documents/${document.id}/versions?page=1&page_size=100`)
      responses.push(versions)
      for (const version of versions.items) {
        responses.push(
          await mockRequest(`/documents/${document.id}/versions/${version.id}`),
          await mockRequest(`/documents/${document.id}/versions/${version.id}/assets?page=1&page_size=100`),
          await mockRequest(`/documents/${document.id}/versions/${version.id}/issues?page=1&page_size=100`),
        )
      }
    }
    for (const job of jobPage.items) {
      responses.push(
        await mockRequest(`/backup-jobs/${job.id}`),
        await mockRequest(`/backup-jobs/${job.id}/subtasks?page=1&page_size=100`),
        await mockRequest(`/backup-jobs/${job.id}/issues?page=1&page_size=100`),
      )
    }
    for (const tombstone of tombstonePage.items) responses.push(await mockRequest(`/deletion-tombstones/${tombstone.id}`))

    const created = await mockRequest<{ credential: Credential; operation: Operation }>('/credentials', {
      method: 'POST',
      body: { name: 'UUID 验证凭据', base_url: 'https://www.yuque.com', token: 'uuid-test-token' },
    })
    responses.push(created, await mockRequest(`/operations/${created.operation.id}`))

    const localIds = collectLocalIds(responses)
    expect(localIds.length).toBeGreaterThan(100)
    localIds.forEach(({ path, value }) => expect(value, path).toMatch(uuidPattern))
  }, 15_000)

  it('keeps credential discovery, repository counts and connection states synchronized', async () => {
    const mockRequest = await freshMock()
    await initialize(mockRequest)
    const created = await mockRequest<{ credential: Credential; operation: Operation }>('/credentials', {
      method: 'POST',
      body: { name: '新增个人凭据', base_url: 'https://www.yuque.com', token: 'new-secret-token' },
    })
    await mockRequest(`/operations/${created.operation.id}`)
    await mockRequest(`/operations/${created.operation.id}`)
    await mockRequest(`/credentials/${created.credential.id}/enable`, { method: 'POST' })

    const discovery = await mockRequest<Operation>(`/credentials/${created.credential.id}/discover-repositories`, { method: 'POST' })
    await mockRequest(`/operations/${discovery.id}`)
    const completed = await mockRequest<Operation>(`/operations/${discovery.id}`)
    expect(completed.result).toEqual({ discovered: 2, created: 0, updated: 2, duplicates: 2, requires_primary_selection: 1 })

    const discoveredCredential = await mockRequest<Credential>(`/credentials/${created.credential.id}`)
    const product = await mockRequest<Repository>(`/repositories/${ids.repositoryProduct}`)
    const engineering = await mockRequest<Repository>(`/repositories/${ids.repositoryEngineering}`)
    expect(discoveredCredential.repository_count).toBe(2)
    expect(product.credential_count).toBe(2)
    expect(engineering.credential_count).toBe(3)
    expect(product.credentials?.map((credential) => credential.id)).toContain(created.credential.id)

    const selected = await mockRequest<Repository>(`/repositories/${ids.repositoryProduct}/primary-credential`, {
      method: 'PUT', body: { credential_id: created.credential.id },
    })
    expect(selected).toMatchObject({ primary_credential_id: created.credential.id, connection_status: 'connected' })

    await mockRequest(`/credentials/${created.credential.id}/disable`, { method: 'POST' })
    await expect(mockRequest<Repository>(`/repositories/${ids.repositoryProduct}`)).resolves.toMatchObject({ connection_status: 'action_required' })
    await mockRequest(`/credentials/${created.credential.id}/enable`, { method: 'POST' })
    await expect(mockRequest<Repository>(`/repositories/${ids.repositoryProduct}`)).resolves.toMatchObject({ connection_status: 'connected' })

    await mockRequest(`/credentials/${created.credential.id}`, { method: 'DELETE' })
    const afterDelete = await mockRequest<Repository>(`/repositories/${ids.repositoryProduct}`)
    expect(afterDelete).toMatchObject({ primary_credential_id: null, credential_count: 1, connection_status: 'action_required' })
    expect(afterDelete.credentials?.map((credential) => credential.id)).not.toContain(created.credential.id)
    await expect(mockRequest(`/credentials/${created.credential.id}`)).rejects.toMatchObject({ status: 404, code: 'CREDENTIAL_NOT_FOUND' })
    await expect(mockRequest<Paginated<Repository>>(`/repositories?page=1&page_size=100&credential_id=${created.credential.id}`)).resolves.toMatchObject({ total: 0 })
  })

  it('validates schedule inputs and recalculates the next three runs', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-07-23T14:30:00Z'))
    try {
      const mockRequest = await freshMock()
      await initialize(mockRequest)
      await expect(mockRequest('/settings/schedule', { method: 'PUT', body: { cron: '60 2 * * *', timezone: 'UTC' } }))
        .rejects.toMatchObject({ status: 422, code: 'INVALID_CRON' })
      await expect(mockRequest('/settings/schedule', { method: 'PUT', body: { cron: '0 2 * * *', timezone: 'Not/AZone' } }))
        .rejects.toMatchObject({ status: 422, code: 'INVALID_TIMEZONE' })
      await expect(mockRequest('/settings/schedule', { method: 'PUT', body: { cron: '0 2 * * *', timezone: 'UTC', extra: true } }))
        .rejects.toMatchObject({ status: 422, code: 'VALIDATION_ERROR' })

      const updated = await mockRequest<ScheduleSetting>('/settings/schedule', {
        method: 'PUT', body: { cron: '15 9 * * 1-5', timezone: 'UTC' },
      })
      expect(updated).toEqual({
        cron: '15 9 * * 1-5',
        timezone: 'UTC',
        next_runs: ['2026-07-24T09:15:00.000Z', '2026-07-27T09:15:00.000Z', '2026-07-28T09:15:00.000Z'],
        updated_at: '2026-07-23T14:30:00.000Z',
      })
    } finally {
      vi.useRealTimers()
    }
  })

  it('rejects invalid retention and storage-limit bodies', async () => {
    const mockRequest = await freshMock()
    await initialize(mockRequest)

    for (const retention_days of [0, -1, 1.5, '15', undefined]) {
      await expect(mockRequest('/settings/retention', { method: 'PUT', body: retention_days === undefined ? {} : { retention_days } }))
        .rejects.toMatchObject({ status: 422, code: 'VALIDATION_ERROR' })
    }
    await expect(mockRequest('/settings/retention', { method: 'PUT', body: { retention_days: 30 } })).resolves.toMatchObject({ retention_days: 30 })

    for (const max_asset_size_bytes of [0, -1, 1.5, '1024', undefined]) {
      await expect(mockRequest('/settings/storage-limit', { method: 'PUT', body: max_asset_size_bytes === undefined ? {} : { max_asset_size_bytes } }))
        .rejects.toMatchObject({ status: 422, code: 'VALIDATION_ERROR' })
    }
    await expect(mockRequest('/settings/storage-limit', { method: 'PUT', body: { max_asset_size_bytes: null } })).resolves.toMatchObject({ max_asset_size_bytes: null, max_asset_size_unlimited: true })
    await expect(mockRequest('/settings/storage-limit', { method: 'PUT', body: { max_asset_size_bytes: 1024 } })).resolves.toMatchObject({ max_asset_size_bytes: 1024, max_asset_size_unlimited: false })
  })

  it('keeps aggregate counts and documented default ordering consistent', async () => {
    const mockRequest = await freshMock()
    await initialize(mockRequest)
    const dashboard = await mockRequest<DashboardSummary>('/dashboard/summary')
    const credentials = await mockRequest<Paginated<Credential>>('/credentials?page=1&page_size=100')
    const repositories = await mockRequest<Paginated<Repository>>('/repositories?page=1&page_size=100')
    const documents = await mockRequest<Paginated<DocumentSummary>>('/documents?page=1&page_size=100')
    const jobs = await mockRequest<Paginated<BackupJob>>('/backup-jobs?page=1&page_size=100')
    const tombstones = await mockRequest<Paginated<Tombstone>>('/deletion-tombstones?page=1&page_size=100')

    expect(credentials.items.map((item) => item.created_at)).toEqual([...credentials.items].sort((left, right) => left.created_at.localeCompare(right.created_at) || left.id.localeCompare(right.id)).map((item) => item.created_at))
    expect(repositories.items.map((item) => item.id)).toEqual([...repositories.items].sort((left, right) => left.name.localeCompare(right.name, 'zh-CN') || left.id.localeCompare(right.id)).map((item) => item.id))
    expect(documents.items.map((item) => item.id)).toEqual([...documents.items].sort((left, right) => left.title.localeCompare(right.title, 'zh-CN') || left.id.localeCompare(right.id)).map((item) => item.id))
    expect(jobs.items.map((item) => item.id)).toEqual([...jobs.items].sort((left, right) => right.created_at.localeCompare(left.created_at) || right.id.localeCompare(left.id)).map((item) => item.id))
    expect(tombstones.items.map((item) => item.id)).toEqual([...tombstones.items].sort((left, right) => right.deleted_at.localeCompare(left.deleted_at) || right.id.localeCompare(left.id)).map((item) => item.id))

    let versionTotal = 0
    for (const repository of repositories.items) {
      const repositoryDocuments = await mockRequest<Paginated<DocumentSummary>>(`/documents?page=1&page_size=100&repository_id=${repository.id}`)
      expect(repository.document_count).toBe(repositoryDocuments.total)
    }
    for (const document of documents.items) {
      const versions = await mockRequest<Paginated<VersionSummary>>(`/documents/${document.id}/versions?page=1&page_size=100`)
      versionTotal += versions.total
      expect(versions.items.map((item) => item.id)).toEqual([...versions.items].sort((left, right) => right.created_at.localeCompare(left.created_at) || right.id.localeCompare(left.id)).map((item) => item.id))
    }
    expect(dashboard).toMatchObject({
      repositories: repositories.items.filter((item) => item.selected).length,
      documents: documents.total,
      versions: versionTotal,
      job_counts: {
        succeeded: jobs.items.filter((item) => item.status === 'succeeded').length,
        partial: jobs.items.filter((item) => item.status === 'partial').length,
        failed: jobs.items.filter((item) => item.status === 'failed').length,
      },
    })
  })
})
