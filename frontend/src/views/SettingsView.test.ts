import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: {
    getRetention: vi.fn(),
    getStorage: vi.fn(),
    updateRetention: vi.fn(),
    updateStorageLimit: vi.fn(),
    updatePassword: vi.fn(),
  },
}))

vi.mock('@/api', () => ({ api, ApiError: class ApiError extends Error {} }))

import SettingsView from './SettingsView.vue'

describe('SettingsView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getRetention.mockResolvedValue({ retention_days: 90, updated_at: '2026-07-24T10:00:00Z' })
    api.getStorage.mockResolvedValue({
      database_path: '/data/db',
      content_path: '/data/content',
      max_asset_size_bytes: 524288000,
      max_asset_size_unlimited: false,
      usage: { database_bytes: 1, version_bytes: 2, asset_bytes: 3, total_bytes: 6 },
      updated_at: '2026-07-24T10:00:00Z',
    })
  })

  it('keeps scheduling out of general settings', async () => {
    const wrapper = mount(SettingsView)
    await flushPromises()

    expect(wrapper.text()).toContain('保留')
    expect(wrapper.text()).toContain('存储')
    expect(wrapper.text()).toContain('账户')
    expect(wrapper.text()).not.toContain('调度')
    expect(wrapper.text()).not.toContain('Cron')
    expect(api.getRetention).toHaveBeenCalledOnce()
    expect(api.getStorage).toHaveBeenCalledOnce()
  })
})
