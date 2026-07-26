import { beforeEach, describe, expect, it, vi } from 'vitest'

const { mockRequest } = vi.hoisted(() => ({ mockRequest: vi.fn().mockResolvedValue({}) }))

vi.mock('./mock', () => ({ mockRequest }))

import { api } from './index'

const credentialId = '00000002-0000-4000-8000-000000000001'
const repositoryId = '00000003-0000-4000-8000-000000000001'
const partialJobId = '00000008-0000-4000-8000-000000000002'

describe('business API contract', () => {
  beforeEach(() => {
    mockRequest.mockReset().mockResolvedValue({})
  })

  it('adds UUID idempotency keys only to the three required operations', async () => {
    await api.initialize('admin', 'correct-password-123')
    await api.createJob({ type: 'all' })
    await api.rerunJob(partialJobId)
    await api.login('admin', 'correct-password-123')

    expect(mockRequest.mock.calls[0]![1]).toMatchObject({ method: 'POST', csrf: false, idempotencyKey: expect.stringMatching(/^[0-9a-f-]{36}$/i) })
    expect(mockRequest.mock.calls[1]![1]).toMatchObject({ method: 'POST', idempotencyKey: expect.stringMatching(/^[0-9a-f-]{36}$/i), body: { scope: { type: 'all' } } })
    expect(mockRequest.mock.calls[2]![1]).toMatchObject({ method: 'POST', idempotencyKey: expect.stringMatching(/^[0-9a-f-]{36}$/i) })
    expect(mockRequest.mock.calls[3]![1]).toMatchObject({ method: 'POST', csrf: false })
    expect(mockRequest.mock.calls[3]![1]).not.toHaveProperty('idempotencyKey')
  })

  it('sends selected repositories to quota estimation without an idempotency key', async () => {
    const scope = {
      type: 'repositories' as const,
      credential_id: credentialId,
      repository_ids: [repositoryId],
    }
    await api.estimateJob(scope)

    expect(mockRequest).toHaveBeenCalledWith('/backup-jobs/estimate', {
      method: 'POST',
      body: { scope },
    })
    expect(mockRequest.mock.calls[0]![1]).not.toHaveProperty('idempotencyKey')
  })

  it('serializes every documented job filter and repeats multiple statuses', async () => {
    await api.getJobs({
      page: 2,
      page_size: 10,
      status: ['running', 'waiting_quota'],
      trigger: 'manual',
      credential_id: credentialId,
      repository_id: repositoryId,
      created_from: '2026-07-01T00:00:00Z',
      created_to: '2026-07-31T23:59:59Z',
    })

    const url = new URL(mockRequest.mock.calls[0]![0], 'http://mock.local')
    expect(url.pathname).toBe('/backup-jobs')
    expect(url.searchParams.get('page')).toBe('2')
    expect(url.searchParams.get('page_size')).toBe('10')
    expect(url.searchParams.getAll('status')).toEqual(['running', 'waiting_quota'])
    expect(url.searchParams.get('trigger')).toBe('manual')
    expect(url.searchParams.get('credential_id')).toBe(credentialId)
    expect(url.searchParams.get('repository_id')).toBe(repositoryId)
    expect(url.searchParams.get('created_from')).toBe('2026-07-01T00:00:00Z')
    expect(url.searchParams.get('created_to')).toBe('2026-07-31T23:59:59Z')
  })

  it('keeps tombstone string search compatibility while supporting full pagination filters', async () => {
    await api.getTombstones('旧版')
    await api.getTombstones({ page: 3, page_size: 5, q: '清单', repository_id: repositoryId, deleted_from: '2026-06-01T00:00:00Z', deleted_to: '2026-06-30T23:59:59Z' })

    const legacy = new URL(mockRequest.mock.calls[0]![0], 'http://mock.local')
    const complete = new URL(mockRequest.mock.calls[1]![0], 'http://mock.local')
    expect(Object.fromEntries(legacy.searchParams)).toEqual({ q: '旧版', page: '1', page_size: '20' })
    expect(Object.fromEntries(complete.searchParams)).toEqual({
      page: '3', page_size: '5', q: '清单', repository_id: repositoryId,
      deleted_from: '2026-06-01T00:00:00Z', deleted_to: '2026-06-30T23:59:59Z',
    })
  })
})
