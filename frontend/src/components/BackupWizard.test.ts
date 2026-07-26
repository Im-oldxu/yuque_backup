import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: {
    getCredentials: vi.fn(),
    getRepositories: vi.fn(),
    getSchedule: vi.fn(),
    estimateJob: vi.fn(),
    createJob: vi.fn(),
    updateRepositorySelection: vi.fn(),
    updateSchedule: vi.fn(),
  },
}))

vi.mock('@/api', () => ({ api, ApiError: class ApiError extends Error {} }))

import BackupWizard from './BackupWizard.vue'

const credential = {
  id: '00000002-0000-4000-8000-000000000001', name: '个人语雀', base_url: 'https://www.yuque.com',
  token_masked: '************Ab3x', subject_type: 'user', subject_id: '1', login: 'admin', status: 'valid',
  enabled: true, last_verified_at: '2026-07-24T10:00:00Z', rate_limit: { limit: 5000, remaining: 4900, observed_at: '2026-07-24T10:00:00Z' },
  next_retry_at: null, active_operation_id: null, repository_count: 1, created_at: '2026-07-24T10:00:00Z', updated_at: '2026-07-24T10:00:00Z',
} as const

const repository = {
  id: '00000003-0000-4000-8000-000000000001', yuque_book_id: '100', base_url: 'https://www.yuque.com',
  name: '产品知识库', slug: 'product', namespace: 'admin/product', selected: true, connection_status: 'connected',
  primary_credential_id: credential.id, credential_count: 1, document_count: 12, last_success_at: '2026-07-23T10:00:00Z', content_updated_at: '2026-07-24T09:00:00Z',
} as const

const estimate = {
  repository_count: 1,
  document_count: 12,
  estimated_api_calls: 16,
  is_precise: false,
  credentials: [{
    credential_id: credential.id, credential_name: credential.name, repository_count: 1, document_count: 12,
    estimated_api_calls: 16, rate_limit_limit: 5000, rate_limit_remaining: 4900,
    rate_limit_observed_at: '2026-07-24T10:00:00Z', snapshot_fresh: true, sufficient: true,
  }],
  calculation_basis: ['按本地已知文档数估算'],
} as const

function button(wrapper: ReturnType<typeof mount>, label: string) {
  const result = wrapper.findAll('button').find((item) => item.text().includes(label))
  if (!result) throw new Error(`button not found: ${label}`)
  return result
}

describe('BackupWizard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getCredentials.mockResolvedValue({ items: [credential], page: 1, page_size: 100, total: 1 })
    api.getRepositories.mockResolvedValue({ items: [repository], page: 1, page_size: 100, total: 1 })
    api.getSchedule.mockResolvedValue({ cron: '0 2 * * *', timezone: 'Asia/Shanghai', next_runs: [], updated_at: '2026-07-24T10:00:00Z' })
    api.estimateJob.mockResolvedValue(estimate)
    api.createJob.mockResolvedValue({ job: { id: 'job-id' }, merged: false })
    api.updateRepositorySelection.mockResolvedValue(repository)
    api.updateSchedule.mockImplementation(async (cron: string, timezone: string) => ({
      cron,
      timezone,
      next_runs: ['2026-07-25T19:15:00Z', '2026-07-26T19:15:00Z', '2026-07-27T19:15:00Z'],
      updated_at: '2026-07-24T10:00:00Z',
    }))
  })

  it('guides a manual backup through credential, repository and quota confirmation', async () => {
    const wrapper = mount(BackupWizard, { props: { mode: 'manual' } })
    await flushPromises()
    expect(wrapper.text()).toContain('个人语雀')
    expect(wrapper.text()).toContain('确认执行')
    expect(wrapper.text()).not.toContain('选择执行时间')

    await button(wrapper, '下一步').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('产品知识库')

    await button(wrapper, '下一步').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('约 16 次')
    expect(wrapper.text()).toContain('额度充足')

    await button(wrapper, '确认并执行').trigger('click')
    await flushPromises()
    expect(api.createJob).toHaveBeenCalledWith({
      type: 'repositories',
      credential_id: credential.id,
      repository_ids: [repository.id],
    })
  })

  it('uses a separate time step and only saves the scheduled plan', async () => {
    const wrapper = mount(BackupWizard, { props: { mode: 'scheduled' } })
    await flushPromises()
    expect(wrapper.text()).toContain('选择执行时间')
    expect(wrapper.text()).toContain('添加定时计划')
    expect(wrapper.text()).toContain('选择凭据')
    expect(wrapper.text()).toContain('选择知识库')

    await button(wrapper, '下一步').trigger('click')
    await flushPromises()
    await button(wrapper, '下一步').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('每日执行时间')
    const time = wrapper.get('input[type="time"]')
    await time.setValue('03:15')
    await button(wrapper, '下一步').trigger('click')
    expect(wrapper.text()).toContain('每天 03:15')
    expect(wrapper.text()).toContain('不会立即创建备份任务')

    await button(wrapper, '添加定时计划').trigger('click')
    await flushPromises()
    expect(api.updateSchedule).toHaveBeenCalledWith('15 3 * * *', 'Asia/Shanghai')
    expect(api.createJob).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('后续三次执行时间')
  })

  it('blocks manual confirmation when a fresh quota snapshot is insufficient', async () => {
    api.estimateJob.mockResolvedValueOnce({
      ...estimate,
      credentials: [{ ...estimate.credentials[0], rate_limit_remaining: 2, sufficient: false }],
    })
    const wrapper = mount(BackupWizard, { props: { mode: 'manual' } })
    await flushPromises()
    await button(wrapper, '下一步').trigger('click')
    await flushPromises()
    await button(wrapper, '下一步').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('额度不足')
    expect(button(wrapper, '确认并执行').attributes('disabled')).toBeDefined()
    expect(api.createJob).not.toHaveBeenCalled()
  })
})
