import { flushPromises, shallowMount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { DocumentDetail, VersionDetail, VersionSummary } from '@/api/types'
import DocumentNavigation from '@/components/DocumentNavigation.vue'
import PageHeader from '@/components/PageHeader.vue'

const apiMock = vi.hoisted(() => ({
  assetDownloadUrl: vi.fn(),
  downloadUrl: vi.fn(),
  getDocument: vi.fn(),
  getPreviewHtml: vi.fn(),
  getRepositories: vi.fn(),
  getToc: vi.fn(),
  getVersion: vi.fn(),
  getVersionAssets: vi.fn(),
  getVersionIssues: vi.fn(),
  getVersions: vi.fn(),
  previewUrl: vi.fn(),
  search: vi.fn(),
}))

vi.mock('@/api', () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

import DocumentView from './DocumentView.vue'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

function documentDetail(id: string, title: string): DocumentDetail {
  return {
    id,
    repository_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    yuque_doc_id: `remote-${id}`,
    type: 'Doc',
    title,
    slug: title,
    path: `/${title}`,
    deleted_at: null,
    purge_at: null,
    latest_version_id: null,
    latest_version_completeness: null,
    updated_at: '2026-07-23T00:00:00Z',
    repository: { id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', name: '测试知识库', namespace: 'test/repository' },
    original_path: `/${title}`,
    remaining_retention_seconds: null,
    latest_successful_version: null,
    version_count: 0,
  }
}

function versionSummary(id: string): VersionSummary {
  return {
    id,
    remote_version_id: 'remote-version-1',
    format: 'markdown',
    content_hash: 'sha256:test',
    completeness: 'complete',
    is_latest: true,
    preview_available: true,
    resource_total: 0,
    resource_downloaded: 0,
    issue_count: 0,
    source_job_id: '33333333-3333-4333-8333-333333333333',
    remote_updated_at: '2026-07-23T00:00:00Z',
    created_at: '2026-07-23T00:00:00Z',
  }
}

function versionDetail(documentId: string, summary: VersionSummary): VersionDetail {
  return {
    ...summary,
    document_id: documentId,
    downloads: { raw_response: true, raw_body: true, offline_html: true },
    asset_summary: { total: 0, downloaded: 0, failed: 0, skipped: 0 },
  }
}

describe('DocumentView request ownership', () => {
  beforeEach(() => {
    apiMock.getRepositories.mockReset().mockResolvedValue({
      items: [{
        id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        yuque_book_id: 'remote-repository',
        base_url: 'https://www.yuque.com',
        name: '测试知识库',
        slug: 'repository',
        namespace: 'test/repository',
        selected: true,
        connection_status: 'connected',
        primary_credential_id: null,
        credential_count: 1,
        document_count: 2,
        last_success_at: '2026-07-23T00:00:00Z',
        content_updated_at: '2026-07-23T00:00:00Z',
      }],
      page: 1,
      page_size: 100,
      total: 1,
    })
    apiMock.search.mockReset().mockResolvedValue({ repositories: [], documents: [] })
  })

  afterEach(() => vi.clearAllMocks())

  it('does not let a slow previous document overwrite the current route', async () => {
    const documentA = '11111111-1111-4111-8111-111111111111'
    const documentB = '22222222-2222-4222-8222-222222222222'
    const slowA = deferred<DocumentDetail>()

    apiMock.getDocument.mockImplementation((id: string) => id === documentA ? slowA.promise : Promise.resolve(documentDetail(documentB, '文档 B')))
    apiMock.getVersions.mockResolvedValue({ items: [], page: 1, page_size: 100, total: 0 })
    apiMock.getToc.mockResolvedValue({ repository_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', updated_at: '2026-07-23T00:00:00Z', items: [] })

    const wrapper = shallowMount(DocumentView, { props: { documentId: documentA } })
    await wrapper.setProps({ documentId: documentB })
    await flushPromises()

    expect(wrapper.findComponent(PageHeader).props('title')).toBe('文档 B')

    slowA.resolve(documentDetail(documentA, '文档 A'))
    await flushPromises()

    expect(wrapper.findComponent(PageHeader).props('title')).toBe('文档 B')
  })

  it('loads only the first version page for a document with a large history', async () => {
    const documentId = '11111111-1111-4111-8111-111111111111'
    const summary = versionSummary('44444444-4444-4444-8444-444444444444')

    apiMock.getDocument.mockResolvedValue(documentDetail(documentId, '长历史文档'))
    apiMock.getVersions.mockResolvedValue({ items: [summary], page: 1, page_size: 50, total: 100_000 })
    apiMock.getVersion.mockResolvedValue(versionDetail(documentId, summary))
    apiMock.getVersionAssets.mockResolvedValue({ items: [], page: 1, page_size: 20, total: 0 })
    apiMock.getVersionIssues.mockResolvedValue({ items: [], page: 1, page_size: 20, total: 0 })
    apiMock.getPreviewHtml.mockResolvedValue('<h1 id="outline">大纲</h1>')
    apiMock.getToc.mockResolvedValue({ repository_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', updated_at: '2026-07-23T00:00:00Z', items: [] })

    shallowMount(DocumentView, { props: { documentId } })
    await flushPromises()

    expect(apiMock.getVersions).toHaveBeenCalledTimes(1)
    expect(apiMock.getVersions).toHaveBeenCalledWith(documentId, { page: 1, page_size: 50 })
  })

  it('opens the latest successful version even when it is outside the first history page', async () => {
    const documentId = '11111111-1111-4111-8111-111111111111'
    const latestSuccessful = versionSummary('44444444-4444-4444-8444-444444444444')
    const newestFailed: VersionSummary = {
      ...versionSummary('55555555-5555-4555-8555-555555555555'),
      completeness: 'failed',
      is_latest: false,
      preview_available: false,
    }
    const detail = documentDetail(documentId, '审计记录很多的文档')
    detail.latest_successful_version = latestSuccessful
    detail.latest_version_id = latestSuccessful.id
    detail.version_count = 51

    apiMock.getDocument.mockResolvedValue(detail)
    apiMock.getVersions.mockResolvedValue({ items: [newestFailed], page: 1, page_size: 50, total: 51 })
    apiMock.getVersion.mockResolvedValue(versionDetail(documentId, latestSuccessful))
    apiMock.getVersionAssets.mockResolvedValue({ items: [], page: 1, page_size: 20, total: 0 })
    apiMock.getVersionIssues.mockResolvedValue({ items: [], page: 1, page_size: 20, total: 0 })
    apiMock.getPreviewHtml.mockResolvedValue('<h1>大纲</h1>')
    apiMock.getToc.mockResolvedValue({ repository_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', updated_at: '2026-07-23T00:00:00Z', items: [] })

    shallowMount(DocumentView, { props: { documentId } })
    await flushPromises()

    expect(apiMock.getVersion).toHaveBeenCalledWith(documentId, latestSuccessful.id)
  })

  it('clears pending reader loaders when switching to a document without versions', async () => {
    localStorage.setItem('yb_reader_navigation', 'open')
    const documentA = '11111111-1111-4111-8111-111111111111'
    const documentB = '22222222-2222-4222-8222-222222222222'
    const summary = versionSummary('44444444-4444-4444-8444-444444444444')
    const detailA = documentDetail(documentA, '文档 A')
    detailA.latest_successful_version = summary
    detailA.latest_version_id = summary.id
    detailA.version_count = 1
    const pendingAssets = deferred<never>()
    const pendingIssues = deferred<never>()
    const pendingOutline = deferred<never>()

    apiMock.getDocument.mockImplementation((id: string) => Promise.resolve(id === documentA ? detailA : documentDetail(documentB, '无版本文档 B')))
    apiMock.getVersions.mockImplementation((id: string) => Promise.resolve(id === documentA
      ? { items: [summary], page: 1, page_size: 50, total: 1 }
      : { items: [], page: 1, page_size: 50, total: 0 }))
    apiMock.getVersion.mockResolvedValue(versionDetail(documentA, summary))
    apiMock.getVersionAssets.mockReturnValue(pendingAssets.promise)
    apiMock.getVersionIssues.mockReturnValue(pendingIssues.promise)
    apiMock.getPreviewHtml.mockReturnValue(pendingOutline.promise)
    apiMock.getToc.mockResolvedValue({ repository_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', updated_at: '2026-07-23T00:00:00Z', items: [] })

    const wrapper = shallowMount(DocumentView, {
      props: { documentId: documentA },
      global: { stubs: { AsyncState: { template: '<div><slot /></div>' } } },
    })
    await flushPromises()
    expect(apiMock.getPreviewHtml).toHaveBeenCalled()

    await wrapper.setProps({ documentId: documentB })
    await flushPromises()

    expect(wrapper.findComponent(PageHeader).props('title')).toBe('无版本文档 B')
    expect(wrapper.findComponent(DocumentNavigation).props('outlineLoading')).toBe(false)
    expect(wrapper.findComponent(DocumentNavigation).props('repositoriesLoading')).toBe(false)
    expect(wrapper.findComponent(DocumentNavigation).props('repositoryTocLoadingIds')).toEqual([])
    expect(wrapper.find('spinner-stub').exists()).toBe(false)
  })
})
