import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import AsyncState from './AsyncState.vue'

describe('AsyncState loading indicator', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('does not flash the loading indicator for a short request', async () => {
    vi.useFakeTimers()
    const wrapper = mount(AsyncState, {
      props: { loading: true },
      slots: { default: '<p data-testid="content">内容</p>' },
    })

    expect(wrapper.find('[role="status"]').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(100)
    await wrapper.setProps({ loading: false })

    expect(wrapper.find('[role="status"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="content"]').text()).toBe('内容')
  })

  it('shows a compact loading indicator when a request remains pending', async () => {
    vi.useFakeTimers()
    const wrapper = mount(AsyncState, { props: { loading: true } })

    await vi.advanceTimersByTimeAsync(180)

    expect(wrapper.get('[role="status"]').attributes('aria-label')).toBe('Loading')
    expect(wrapper.find('[data-slot="skeleton"]').exists()).toBe(false)
  })
})
