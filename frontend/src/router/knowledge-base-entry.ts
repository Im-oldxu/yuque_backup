import type { DocumentListParams, DocumentSummary, Paginated, Repository, RepositoryListParams, TocNode, TocTree } from '@/api'

interface KnowledgeBaseEntryApi {
  getDocuments: (filters: DocumentListParams) => Promise<Paginated<DocumentSummary>>
  getRepositories: (filters: RepositoryListParams) => Promise<Paginated<Repository>>
  getToc: (repositoryId: string) => Promise<TocTree>
}

const PAGE_SIZE = 100

export function firstDocumentId(nodes: TocNode[]): string | null {
  for (const node of nodes) {
    if (node.document_id) return node.document_id
    const childDocumentId = firstDocumentId(node.children)
    if (childDocumentId) return childDocumentId
  }
  return null
}

async function firstRepositoryDocumentId(apiClient: KnowledgeBaseEntryApi, repository: Repository): Promise<string | null> {
  try {
    const toc = await apiClient.getToc(repository.id)
    const documentId = firstDocumentId(toc.items)
    if (documentId) return documentId
  } catch {
    // A stale or unavailable TOC must not block access to already backed-up documents.
  }

  const documents = await apiClient.getDocuments({ repository_id: repository.id, page: 1, page_size: 1 })
  return documents.items[0]?.id ?? null
}

export async function resolveKnowledgeBaseEntry(apiClient: KnowledgeBaseEntryApi): Promise<string | null> {
  let page = 1
  let loaded = 0
  let total = 0

  do {
    const repositories = await apiClient.getRepositories({ page, page_size: PAGE_SIZE })
    total = repositories.total
    loaded += repositories.items.length

    for (const repository of repositories.items) {
      if (repository.document_count < 1) continue
      const documentId = await firstRepositoryDocumentId(apiClient, repository)
      if (documentId) return documentId
    }

    if (!repositories.items.length) break
    page += 1
  } while (loaded < total)

  return null
}
