import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, httpRequest, httpTextRequest, setAuthRequiredHandler } from './client'

describe('HTTP API client', () => {
  beforeEach(() => {
    document.cookie = 'yb_csrf=; Max-Age=0; Path=/'
    setAuthRequiredHandler(undefined)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    setAuthRequiredHandler(undefined)
  })

  it('sends cookie credentials, JSON, CSRF and idempotency headers for a session write', async () => {
    document.cookie = 'yb_csrf=csrf%20value; Path=/'
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await httpRequest('/backup-jobs', {
      method: 'POST',
      body: { scope: { type: 'all' } },
      idempotencyKey: '11111111-1111-4111-8111-111111111111',
    })

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const headers = new Headers(init.headers)
    expect(url).toBe('/api/v1/backup-jobs')
    expect(init.credentials).toBe('include')
    expect(init.method).toBe('POST')
    expect(init.body).toBe(JSON.stringify({ scope: { type: 'all' } }))
    expect(headers.get('Accept')).toBe('application/json')
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(headers.get('X-CSRF-Token')).toBe('csrf value')
    expect(headers.get('Idempotency-Key')).toBe('11111111-1111-4111-8111-111111111111')
  })

  it('can explicitly omit CSRF for public initialization and login writes', async () => {
    document.cookie = 'yb_csrf=stale-token; Path=/'
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'admin-1' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await httpRequest('/auth/login', { method: 'POST', csrf: false, body: { username: 'admin', password: 'password' } })

    const init = fetchMock.mock.calls[0]![1] as RequestInit
    expect(new Headers(init.headers).has('X-CSRF-Token')).toBe(false)
  })

  it('maps 204 to undefined and returns text previews with cookie credentials', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response('<article>preview</article>', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(httpRequest('/auth/logout', { method: 'POST' })).resolves.toBeUndefined()
    await expect(httpTextRequest('/documents/00000004-0000-4000-8000-000000000001/versions/00000005-0000-4000-8000-000000000001/preview')).resolves.toBe('<article>preview</article>')
    expect((fetchMock.mock.calls[1]![1] as RequestInit).credentials).toBe('include')
  })

  it('preserves structured error details and clears the session on AUTH_REQUIRED', async () => {
    const onAuthRequired = vi.fn()
    setAuthRequiredHandler(onAuthRequired)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 'AUTH_REQUIRED',
      message: '登录状态已失效。',
      request_id: 'req-auth',
      field_errors: [{ field: 'session', reason: 'expired' }],
      retry_after_seconds: 30,
    }), { status: 401, headers: { 'Content-Type': 'application/json' } })))

    const error = await httpRequest('/dashboard/summary').catch((reason: unknown) => reason)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 401,
      code: 'AUTH_REQUIRED',
      requestId: 'req-auth',
      retryAfterSeconds: 30,
      fieldErrors: [{ field: 'session', reason: 'expired' }],
    })
    expect(onAuthRequired).toHaveBeenCalledOnce()
  })

  it('uses a safe fallback for non-JSON and malformed error bodies', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response('upstream error', { status: 503, headers: { 'X-Request-ID': 'req-text' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ message: 'missing contract fields' }), { status: 500, headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'req-shape' } }))
    vi.stubGlobal('fetch', fetchMock)

    const textError = await httpRequest('/dashboard/summary').catch((reason: unknown) => reason)
    const shapeError = await httpRequest('/dashboard/summary').catch((reason: unknown) => reason)

    expect(textError).toMatchObject({ code: 'INTERNAL_ERROR', requestId: 'req-text', message: '请求未完成，请稍后重试。' })
    expect(shapeError).toMatchObject({ code: 'INTERNAL_ERROR', requestId: 'req-shape', message: '请求未完成，请稍后重试。' })
  })
})
