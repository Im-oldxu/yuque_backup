import { describe, expect, it, vi } from 'vitest'
import type { Repository, TocNode } from '@/api'
import { firstDocumentId, resolveKnowledgeBaseEntry } from './knowledge-base-entry'

function repository(id: string, name: string, documentCount: number): Repository {
  return {
    id,
    yuque_book_id: `remote-${id}`,
    base_url: 'https://www.yuque.com',
    name,
    slug: name,
    namespace: `team/${name}`,
    selected: true,
    connection_status: 'connected',
    primary_credential_id: null,
    credential_count: 1,
    document_count: documentCount,
    last_success_at: null,
    content_updated_at: null,
  }
}

describe('knowledge base entry resolution', () => {
  it('finds the first nested document in official TOC order', () => {
    const nodes: TocNode[] = [{
      id: 'group',
      type: 'TITLE',
      title: '运维与恢复',
      document_id: null,
      path: '/运维与恢复',
      children: [{
        id: 'document-node',
        type: 'DOC',
        title: '备份与恢复操作手册',
        document_id: 'document-1',
        path: '/运维与恢复/备份与恢复操作手册',
        children: [],
      }],
    }]

    expect(firstDocumentId(nodes)).toBe('document-1')
  })

  it('skips empty repositories and opens the first document from the first available TOC', async () => {
    const emptyRepository = repository('repository-empty', '历史归档', 0)
    const productRepository = repository('repository-product', '产品知识库', 2)
    const apiClient = {
      getRepositories: vi.fn().mockResolvedValue({ items: [emptyRepository, productRepository], page: 1, page_size: 100, total: 2 }),
      getToc: vi.fn().mockResolvedValue({
        repository_id: productRepository.id,
        updated_at: '2026-07-23T00:00:00Z',
        items: [{ id: 'node-1', type: 'DOC', title: '备份与恢复操作手册', document_id: 'document-1', path: '/备份与恢复操作手册', children: [] }],
      }),
      getDocuments: vi.fn(),
    }

    await expect(resolveKnowledgeBaseEntry(apiClient)).resolves.toBe('document-1')
    expect(apiClient.getToc).toHaveBeenCalledWith(productRepository.id)
    expect(apiClient.getDocuments).not.toHaveBeenCalled()
  })

  it('falls back to the repository document list when its TOC cannot be read', async () => {
    const productRepository = repository('repository-product', '产品知识库', 1)
    const apiClient = {
      getRepositories: vi.fn().mockResolvedValue({ items: [productRepository], page: 1, page_size: 100, total: 1 }),
      getToc: vi.fn().mockRejectedValue(new Error('TOC unavailable')),
      getDocuments: vi.fn().mockResolvedValue({
        items: [{ id: 'document-1' }],
        page: 1,
        page_size: 1,
        total: 1,
      }),
    }

    await expect(resolveKnowledgeBaseEntry(apiClient as never)).resolves.toBe('document-1')
    expect(apiClient.getDocuments).toHaveBeenCalledWith({ repository_id: productRepository.id, page: 1, page_size: 1 })
  })
})
