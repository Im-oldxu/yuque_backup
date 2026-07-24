import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { Repository, SearchResults, TocTree } from '@/api'
import DocumentNavigation from './DocumentNavigation.vue'

const repositoryA: Repository = {
  id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  yuque_book_id: 'remote-a',
  base_url: 'https://www.yuque.com',
  name: '产品知识库',
  slug: 'product',
  namespace: 'team/product',
  selected: true,
  connection_status: 'connected',
  primary_credential_id: null,
  credential_count: 1,
  document_count: 2,
  last_success_at: '2026-07-23T00:00:00Z',
  content_updated_at: '2026-07-23T00:00:00Z',
}

const repositoryB: Repository = {
  ...repositoryA,
  id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  yuque_book_id: 'remote-b',
  name: '研发知识库',
  slug: 'engineering',
  namespace: 'team/engineering',
  document_count: 1,
}

const productToc: TocTree = {
  repository_id: repositoryA.id,
  updated_at: '2026-07-23T00:00:00Z',
  items: [{
    id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
    type: 'TITLE',
    title: '运维与恢复',
    document_id: null,
    path: '/运维与恢复',
    children: [{
      id: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
      type: 'DOC',
      title: '备份与恢复操作手册',
      document_id: '11111111-1111-4111-8111-111111111111',
      path: '/运维与恢复/备份与恢复操作手册',
      children: [],
    }],
  }],
}

function mountNavigation(searchResults: SearchResults | null = null, searchTerm = '') {
  return mount(DocumentNavigation, {
    props: {
      activeDocumentId: '11111111-1111-4111-8111-111111111111',
      activeRepositoryId: repositoryA.id,
      outline: [],
      repositories: [repositoryA, repositoryB],
      repositoryTocErrors: {},
      repositoryTocLoadingIds: [],
      repositoryTocs: { [repositoryA.id]: productToc },
      searchQuery: searchTerm,
      searchResults,
      searchTerm,
    },
    global: {
      stubs: {
        RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
      },
    },
  })
}

describe('DocumentNavigation', () => {
  it('shows all knowledge bases and the active repository groups by default', () => {
    const wrapper = mountNavigation()

    expect(wrapper.text()).toContain('产品知识库')
    expect(wrapper.text()).toContain('研发知识库')
    expect(wrapper.text()).toContain('运维与恢复')
    expect(wrapper.text()).toContain('备份与恢复操作手册')
    expect(wrapper.get('input').attributes('placeholder')).toBe('搜索知识库文档')
  })

  it('loads another repository TOC only when that repository is expanded', async () => {
    const wrapper = mountNavigation()
    const repositoryButton = wrapper.get(`button[title="${repositoryB.name}"]`)

    expect(repositoryButton.attributes('aria-expanded')).toBe('false')
    await repositoryButton.trigger('click')

    expect(repositoryButton.attributes('aria-expanded')).toBe('true')
    expect(wrapper.emitted('requestToc')).toContainEqual([repositoryB.id])
  })

  it('submits a keyword and renders document results grouped by knowledge base', async () => {
    const results: SearchResults = {
      repositories: [],
      documents: [{
        id: '11111111-1111-4111-8111-111111111111',
        repository_id: repositoryA.id,
        yuque_doc_id: 'remote-document',
        type: 'Doc',
        title: '备份与恢复操作手册',
        slug: 'backup-restore',
        path: '/运维与恢复/备份与恢复操作手册',
        deleted_at: null,
        purge_at: null,
        latest_version_id: null,
        latest_version_completeness: 'complete',
        updated_at: '2026-07-23T00:00:00Z',
      }],
    }
    const wrapper = mountNavigation(results, '恢复')

    await wrapper.get('form[role="search"]').trigger('submit')

    expect(wrapper.emitted('search')).toEqual([['恢复']])
    expect(wrapper.text()).toContain('产品知识库')
    expect(wrapper.text()).toContain('备份与恢复操作手册')
    expect(wrapper.get('a').attributes('href')).toBe('/documents/11111111-1111-4111-8111-111111111111')
  })
})
