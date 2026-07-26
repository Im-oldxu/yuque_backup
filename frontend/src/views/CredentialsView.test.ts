import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Credential, Operation } from '@/api/types'

const { api } = vi.hoisted(() => ({
  api: {
    createCredential: vi.fn(),
    deleteCredential: vi.fn(),
    disableCredential: vi.fn(),
    discoverRepositories: vi.fn(),
    enableCredential: vi.fn(),
    getCredentials: vi.fn(),
    getDashboard: vi.fn(),
    getOperation: vi.fn(),
    updateCredential: vi.fn(),
    verifyCredential: vi.fn(),
  },
}))

vi.mock('@/api', () => ({
  api,
  API_MODE: 'mock',
  ApiError: class ApiError extends Error {},
}))

import CredentialsView from './CredentialsView.vue'

function credential(overrides: Partial<Credential> = {}): Credential {
  return {
    id: '00000002-0000-4000-8000-000000000001',
    name: '个人语雀',
    base_url: 'https://www.yuque.com',
    token_masked: '************Ab3x',
    subject_type: 'user',
    subject_id: '1',
    login: 'admin',
    status: 'valid',
    enabled: true,
    last_verified_at: '2026-07-25T08:00:00Z',
    rate_limit: { limit: 5000, remaining: 0, observed_at: '2026-07-25T08:00:00Z' },
    next_retry_at: null,
    active_operation_id: null,
    repository_count: 21,
    created_at: '2026-07-24T02:00:00Z',
    updated_at: '2026-07-25T08:00:00Z',
    ...overrides,
  }
}

function operation(overrides: Partial<Operation> = {}): Operation {
  return {
    id: '00000009-0000-4000-8000-000000000001',
    type: 'credential_verify',
    status: 'queued',
    credential_id: '00000002-0000-4000-8000-000000000001',
    result: null,
    error: null,
    next_retry_at: null,
    created_at: '2026-07-25T08:33:57Z',
    started_at: null,
    finished_at: null,
    ...overrides,
  }
}

function firstControl(wrapper: ReturnType<typeof mount>, ariaLabel: string) {
  return wrapper.findAll(`[aria-label="${ariaLabel}"]`)[0]!
}

describe('CredentialsView operation states', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getDashboard.mockResolvedValue({ worker: { status: 'online', last_heartbeat_at: '2026-07-25T08:34:00Z' } })
  })

  afterEach(() => vi.useRealTimers())

  it('explains a queued verification when the worker is offline', async () => {
    vi.useFakeTimers()
    const item = credential({ status: 'unverified', enabled: false, active_operation_id: '00000009-0000-4000-8000-000000000001' })
    api.getCredentials.mockResolvedValue({ items: [item], page: 1, page_size: 10, total: 1 })
    api.getDashboard.mockResolvedValue({ worker: { status: 'offline', last_heartbeat_at: '2026-07-25T08:33:27Z' } })
    api.getOperation.mockResolvedValueOnce(operation()).mockReturnValue(new Promise(() => {}))

    const wrapper = mount(CredentialsView)
    await flushPromises()

    expect(wrapper.text()).toContain('后台任务服务离线')
    expect(wrapper.text()).toContain('凭据验证：排队中')
    expect(wrapper.text()).toContain('后台任务服务离线，恢复后将自动继续。')
    expect(firstControl(wrapper, '重新验证：个人语雀').attributes('disabled')).toBeDefined()
    expect(firstControl(wrapper, '重新验证：个人语雀').attributes('title')).toBe('凭据验证尚未完成，完成后才能检查额度')
    expect(firstControl(wrapper, '编辑凭据').attributes('disabled')).toBeUndefined()
    expect(firstControl(wrapper, '删除凭据').attributes('disabled')).toBeUndefined()
    expect(firstControl(wrapper, '个人语雀启用').attributes('title')).toBe('凭据验证有效后才能启用')

    wrapper.unmount()
  })

  it('shows an unknown quota when Yuque returned 429 without a quota snapshot', async () => {
    vi.useFakeTimers()
    const nextRetryAt = '2026-07-25T16:00:00Z'
    const item = credential({ status: 'waiting_quota', rate_limit: null, next_retry_at: nextRetryAt, active_operation_id: '00000009-0000-4000-8000-000000000001' })
    api.getCredentials.mockResolvedValue({ items: [item], page: 1, page_size: 10, total: 1 })
    api.getOperation.mockResolvedValueOnce(operation({ status: 'waiting_quota', next_retry_at: nextRetryAt })).mockReturnValue(new Promise(() => {}))
    api.verifyCredential.mockResolvedValue(operation())

    const wrapper = mount(CredentialsView)
    await flushPromises()

    expect(wrapper.text()).toContain('语雀 API 当前受限')
    expect(wrapper.text()).toContain('语雀返回了 429，但未提供可用的剩余额度信息。')
    expect(wrapper.text()).not.toContain('今日语雀额度已用完')
    expect(wrapper.text()).toContain('下次自动重试：')
    expect(wrapper.text()).not.toContain('凭据验证：等待额度')
    const desktopCredentialRow = wrapper.find(`[data-credential-row="${item.id}"]`)
    const desktopQuotaRow = wrapper.find(`[data-quota-row="${item.id}"]`)
    expect(desktopCredentialRow.exists()).toBe(true)
    expect(desktopCredentialRow.findAll('td')[3]?.text()).toContain('未知')
    expect(desktopCredentialRow.find('[role="alert"]').exists()).toBe(false)
    expect(desktopQuotaRow.exists()).toBe(true)
    expect(desktopQuotaRow.find('td').attributes('colspan')).toBe('7')
    expect(desktopQuotaRow.find('[role="status"]').text()).toContain('期间不会重复请求')
    expect(firstControl(wrapper, '检查额度：个人语雀').attributes('disabled')).toBeUndefined()
    expect(firstControl(wrapper, '检查额度：个人语雀').attributes('title')).toBe('立即检查语雀额度是否恢复')
    await firstControl(wrapper, '检查额度：个人语雀').trigger('click')
    await flushPromises()
    expect(api.verifyCredential).toHaveBeenCalledWith(item.id)
    expect(wrapper.findAll('[aria-label="重新验证"]').length).toBe(0)
    expect(firstControl(wrapper, '个人语雀停用').attributes('disabled')).toBeUndefined()
    expect(firstControl(wrapper, '个人语雀停用').attributes('title')).toBe('停用凭据并取消当前任务')
    expect(firstControl(wrapper, '编辑凭据').attributes('disabled')).toBeUndefined()
    expect(firstControl(wrapper, '删除凭据').attributes('disabled')).toBeUndefined()

    wrapper.unmount()
  })

  it('shows exhausted wording only when the quota snapshot confirms zero remaining', async () => {
    const nextRetryAt = '2026-07-25T16:00:00Z'
    const item = credential({
      status: 'waiting_quota',
      next_retry_at: nextRetryAt,
      rate_limit: { limit: 5000, remaining: 0, observed_at: '2026-07-25T08:00:00Z' },
    })
    api.getCredentials.mockResolvedValue({ items: [item], page: 1, page_size: 10, total: 1 })

    const wrapper = mount(CredentialsView)
    await flushPromises()

    expect(wrapper.text()).toContain('今日语雀额度已用完')
    expect(wrapper.text()).toContain('自动任务将在次日再尝试，期间不会重复请求。')
    expect(wrapper.text()).not.toContain('语雀返回了 429')
    const desktopCredentialRow = wrapper.find(`[data-credential-row="${item.id}"]`)
    expect(desktopCredentialRow.findAll('td')[3]?.text()).toContain('0 / 5000')

    wrapper.unmount()
  })

  it('requires a disabled credential to be verified before it can be enabled again', async () => {
    const item = credential({ status: 'disabled', enabled: false })
    api.getCredentials.mockResolvedValue({ items: [item], page: 1, page_size: 10, total: 1 })

    const wrapper = mount(CredentialsView)
    await flushPromises()

    expect(firstControl(wrapper, '个人语雀启用').attributes('disabled')).toBeDefined()
    expect(firstControl(wrapper, '个人语雀启用').attributes('title')).toBe('凭据验证有效后才能启用')
    expect(firstControl(wrapper, '重新验证：个人语雀').attributes('disabled')).toBeUndefined()

    wrapper.unmount()
  })
})
