import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'
import App from './App.vue'

function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>((done) => { resolve = done })
  return { promise, resolve }
}

describe('App initial navigation', () => {
  it('keeps a stable loading screen visible until the initial route is ready', async () => {
    const navigation = deferred()
    const routePage = defineComponent({ template: '<main data-testid="route-page">页面内容</main>' })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: routePage }],
    })
    router.beforeEach(() => navigation.promise)

    const wrapper = mount(App, {
      global: {
        plugins: [router],
        stubs: {
          TooltipProvider: { template: '<div><slot /></div>' },
          Toaster: true,
        },
      },
    })
    await flushPromises()

    expect(wrapper.get('[aria-label="正在加载应用"]').text()).toContain('Yuque Backup')
    expect(wrapper.find('[data-testid="route-page"]').exists()).toBe(false)

    navigation.resolve()
    await router.isReady()
    await flushPromises()

    expect(wrapper.find('[aria-label="正在加载应用"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="route-page"]').text()).toBe('页面内容')
  })
})
