import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { ApiError, notifyAuthRequired } from '@/api/client'
import type { Admin } from '@/api/types'

const { apiMock } = vi.hoisted(() => ({
  apiMock: {
    getInitialization: vi.fn(),
    getMe: vi.fn(),
    initialize: vi.fn(),
    login: vi.fn(),
    logout: vi.fn(),
  },
}))

vi.mock('@/api', () => ({ api: apiMock }))

import { useSessionStore } from './session'

const administrator: Admin = {
  id: '00000001-0000-4000-8000-000000000001',
  username: 'admin',
  created_at: '2026-07-23T14:30:00Z',
  password_changed_at: null,
}

describe('session store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    Object.values(apiMock).forEach((mock) => mock.mockReset())
  })

  it('clears a stale administrator when the system is no longer initialized', async () => {
    apiMock.login.mockResolvedValue(administrator)
    const session = useSessionStore()
    await session.login('admin', 'correct-password-123')
    apiMock.getInitialization.mockResolvedValue({ initialized: false })

    await session.bootstrap(true)

    expect(session.systemInitialized).toBe(false)
    expect(session.administrator).toBeNull()
    expect(session.bootstrapped).toBe(true)
  })

  it('keeps bootstrap retryable after an unexpected initialization failure', async () => {
    apiMock.getInitialization.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ initialized: false })
    const session = useSessionStore()

    await expect(session.bootstrap()).rejects.toThrow('offline')
    expect(session.bootstrapped).toBe(false)
    expect(session.loading).toBe(false)
    expect(session.bootstrapError).toBe('offline')
    await expect(session.bootstrap()).resolves.toBeUndefined()
    expect(apiMock.getInitialization).toHaveBeenCalledTimes(2)
    expect(session.bootstrapped).toBe(true)
    expect(session.bootstrapError).toBeNull()
  })

  it('treats /auth/me 401 as an anonymous completed bootstrap', async () => {
    apiMock.getInitialization.mockResolvedValue({ initialized: true })
    apiMock.getMe.mockRejectedValue(new ApiError(401, { code: 'AUTH_REQUIRED', message: 'expired', request_id: 'req-1' }))
    const session = useSessionStore()

    await session.bootstrap()

    expect(session.systemInitialized).toBe(true)
    expect(session.isAuthenticated).toBe(false)
    expect(session.bootstrapped).toBe(true)
  })

  it('clears the administrator when the API client reports an expired session', async () => {
    apiMock.login.mockResolvedValue(administrator)
    const session = useSessionStore()
    await session.login('admin', 'correct-password-123')
    expect(session.isAuthenticated).toBe(true)

    notifyAuthRequired()

    expect(session.administrator).toBeNull()
    expect(session.bootstrapped).toBe(true)
  })

  it('always clears local state when logout cannot be confirmed', async () => {
    apiMock.login.mockResolvedValue(administrator)
    apiMock.logout.mockRejectedValue(new Error('network'))
    const session = useSessionStore()
    await session.login('admin', 'correct-password-123')

    await expect(session.logout()).rejects.toThrow('network')
    expect(session.administrator).toBeNull()
  })
})
