import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import TocTreeNode from './TocTreeNode.vue'

describe('TocTreeNode', () => {
  it('keeps a document node navigable while allowing its children to collapse', async () => {
    const wrapper = mount(TocTreeNode, {
      props: {
        activeDocumentId: '11111111-1111-4111-8111-111111111111',
        documentStatuses: {
          '11111111-1111-4111-8111-111111111111': {
            id: '11111111-1111-4111-8111-111111111111',
            repository_id: 'repository',
            yuque_doc_id: 'remote-document',
            type: 'Doc',
            title: '父文档',
            slug: 'parent',
            path: '/父文档',
            deleted_at: null,
            purge_at: null,
            latest_version_id: null,
            latest_version_completeness: null,
            updated_at: '2026-07-23T00:00:00Z',
          },
        },
        node: {
          id: '22222222-2222-4222-8222-222222222222',
          type: 'DOC',
          title: '父文档',
          document_id: '11111111-1111-4111-8111-111111111111',
          path: '/父文档',
          children: [{
            id: '33333333-3333-4333-8333-333333333333',
            type: 'DOC',
            title: '子文档',
            document_id: '44444444-4444-4444-8444-444444444444',
            path: '/父文档/子文档',
            children: [],
          }],
        },
      },
      global: {
        stubs: {
          RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
        },
      },
    })

    expect(wrapper.text()).toContain('父文档')
    expect(wrapper.text()).toContain('子文档')
    expect(wrapper.find('a').attributes('href')).toBe('/documents/11111111-1111-4111-8111-111111111111')
    expect(wrapper.get('[title="未备份正文"]').attributes('title')).toBe('未备份正文')

    const toggle = wrapper.get('button[aria-expanded="true"]')
    await toggle.trigger('click')

    expect(toggle.attributes('aria-expanded')).toBe('false')
    expect(wrapper.text()).not.toContain('子文档')
  })
})
