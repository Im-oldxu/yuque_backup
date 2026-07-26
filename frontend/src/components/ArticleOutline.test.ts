import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ArticleOutline from './ArticleOutline.vue'

describe('ArticleOutline', () => {
  it('marks the active heading and emits the selected heading', async () => {
    const headings = [
      { id: 'overview', level: 1, text: '概览' },
      { id: 'install', level: 2, text: '安装' },
    ]
    const wrapper = mount(ArticleOutline, {
      props: { activeHeadingId: 'install', headings },
    })

    expect(wrapper.get('button[aria-current="location"]').text()).toBe('安装')
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('select')).toEqual([[headings[0]]])
  })
})
