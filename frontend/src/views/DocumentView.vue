<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  Alert, AlertDescription, AlertTitle, Button, Sheet, SheetContent, SheetDescription, SheetHeader,
  SheetTitle, Spinner, Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
  Tabs, TabsContent, TabsList, TabsTrigger,
} from '@/components/ui'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  AlertTriangle, ArrowLeft, Check, ChevronLeft, ChevronRight, ChevronsUpDown, Download, ExternalLink,
  FileJson, FileText, GlobeLock, PanelLeftClose, PanelLeftOpen, Paperclip,
} from 'lucide-vue-next'
import {
  api, ApiError, type AssetReference, type BackupIssue, type DocumentDetail, type Repository,
  type SearchResults, type TocTree, type VersionDetail, type VersionSummary,
} from '@/api'
import AsyncState from '@/components/AsyncState.vue'
import DocumentNavigation from '@/components/DocumentNavigation.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { formatBytes, formatDateTime, formatDuration } from '@/utils/format'
import { extractPreviewOutline, type PreviewHeading } from '@/utils/preview-outline'

const props = defineProps<{ documentId: string }>()
const router = useRouter()
const document = ref<DocumentDetail | null>(null)
const directoryRepositories = ref<Repository[]>([])
const repositoryTocs = ref<Record<string, TocTree | undefined>>({})
const repositoryTocErrors = ref<Record<string, string | undefined>>({})
const repositoryTocLoadingIds = ref<string[]>([])
const directorySearchQuery = ref('')
const directorySearchTerm = ref('')
const directorySearchResults = ref<SearchResults | null>(null)
const versions = ref<VersionSummary[]>([])
const versionPage = ref(1)
const versionTotal = ref(0)
const versionListLoading = ref(false)
const versionListError = ref('')
const versionListFailedPage = ref<number | null>(null)
const versionPickerOpen = ref(false)
const selected = ref<VersionDetail | null>(null)
const selectedVersionId = ref('')
const assets = ref<AssetReference[]>([])
const issues = ref<BackupIssue[]>([])
const outline = ref<PreviewHeading[]>([])
const loading = ref(true)
const versionLoading = ref(false)
const error = ref('')
const versionError = ref('')
const assetError = ref('')
const issueError = ref('')
const outlineError = ref('')
const repositoriesError = ref('')
const directorySearchError = ref('')
const assetLoading = ref(false)
const issueLoading = ref(false)
const outlineLoading = ref(false)
const repositoriesLoading = ref(false)
const directorySearchLoading = ref(false)
const assetPageNumber = ref(1)
const issuePageNumber = ref(1)
const assetTotal = ref(0)
const issueTotal = ref(0)
const navigationOpen = ref(localStorage.getItem('yb_reader_navigation') !== 'closed')
const mobileNavigationOpen = ref(false)
const activeTab = ref('preview')
const previewFragment = ref('')
let loadRequest = 0
let versionRequest = 0
let versionListRequest = 0
let repositoryDirectoryRequest = 0
let directorySearchRequest = 0
const repositoryTocRequests = new Map<string, number>()
let assetRequest = 0
let issueRequest = 0
let outlineRequest = 0

const previewSrc = computed(() => {
  if (!selected.value) return ''
  const base = api.previewUrl(props.documentId, selected.value.id)
  return previewFragment.value ? `${base}#${previewFragment.value}` : base
})

const workspaceColumns = computed(() => navigationOpen.value
  ? 'lg:grid-cols-[280px_minmax(0,1fr)]'
  : 'lg:grid-cols-[minmax(0,1fr)]')
const versionPageSize = 50
const versionPageCount = computed(() => Math.max(1, Math.ceil(versionTotal.value / versionPageSize)))

function setRepositoryTocLoading(repositoryId: string, loadingState: boolean) {
  const ids = new Set(repositoryTocLoadingIds.value)
  if (loadingState) ids.add(repositoryId)
  else ids.delete(repositoryId)
  repositoryTocLoadingIds.value = [...ids]
}

async function loadRepositoryToc(repositoryId: string, force = false, parentLoadRequest = loadRequest) {
  if (!force && repositoryTocs.value[repositoryId]) return
  const request = (repositoryTocRequests.get(repositoryId) ?? 0) + 1
  repositoryTocRequests.set(repositoryId, request)
  setRepositoryTocLoading(repositoryId, true)
  repositoryTocErrors.value = { ...repositoryTocErrors.value, [repositoryId]: undefined }
  try {
    const result = await api.getToc(repositoryId)
    if (repositoryTocRequests.get(repositoryId) === request && parentLoadRequest === loadRequest) {
      repositoryTocs.value = { ...repositoryTocs.value, [repositoryId]: result }
    }
  } catch (cause) {
    if (repositoryTocRequests.get(repositoryId) === request && parentLoadRequest === loadRequest) {
      repositoryTocErrors.value = {
        ...repositoryTocErrors.value,
        [repositoryId]: cause instanceof ApiError ? cause.message : '知识库目录加载失败。',
      }
    }
  } finally {
    if (repositoryTocRequests.get(repositoryId) === request) setRepositoryTocLoading(repositoryId, false)
  }
}

async function loadDirectoryRepositories(parentLoadRequest = loadRequest) {
  const request = ++repositoryDirectoryRequest
  repositoriesLoading.value = true
  repositoriesError.value = ''
  try {
    const items: Repository[] = []
    let page = 1
    let total = 0
    do {
      const result = await api.getRepositories({ page, page_size: 100 })
      if (request !== repositoryDirectoryRequest || parentLoadRequest !== loadRequest) return
      items.push(...result.items)
      total = result.total
      page += 1
      if (!result.items.length) break
    } while (items.length < total)
    directoryRepositories.value = items
  } catch (cause) {
    if (request === repositoryDirectoryRequest && parentLoadRequest === loadRequest) {
      repositoriesError.value = cause instanceof ApiError ? cause.message : '知识库列表加载失败。'
    }
  } finally {
    if (request === repositoryDirectoryRequest) repositoriesLoading.value = false
  }
}

async function loadRepositoryDirectory(repositoryId: string, parentLoadRequest = loadRequest) {
  await Promise.all([
    loadDirectoryRepositories(parentLoadRequest),
    loadRepositoryToc(repositoryId, false, parentLoadRequest),
  ])
}

async function searchRepositoryDirectory(query: string) {
  const term = query.trim()
  directorySearchQuery.value = term
  directorySearchTerm.value = term
  if (!term) {
    clearRepositoryDirectorySearch()
    return
  }

  const request = ++directorySearchRequest
  directorySearchLoading.value = true
  directorySearchError.value = ''
  try {
    const result = await api.search(term)
    if (request === directorySearchRequest && term === directorySearchTerm.value) directorySearchResults.value = result
  } catch (cause) {
    if (request === directorySearchRequest && term === directorySearchTerm.value) {
      directorySearchError.value = cause instanceof ApiError ? cause.message : '知识库搜索失败。'
      directorySearchResults.value = null
    }
  } finally {
    if (request === directorySearchRequest) directorySearchLoading.value = false
  }
}

function clearRepositoryDirectorySearch() {
  directorySearchRequest += 1
  directorySearchQuery.value = ''
  directorySearchTerm.value = ''
  directorySearchResults.value = null
  directorySearchError.value = ''
  directorySearchLoading.value = false
}

async function load() {
  const request = ++loadRequest
  const documentId = props.documentId
  versionRequest += 1
  versionListRequest += 1
  repositoryDirectoryRequest += 1
  directorySearchRequest += 1
  repositoryTocRequests.clear()
  assetRequest += 1
  issueRequest += 1
  outlineRequest += 1
  loading.value = true
  versionLoading.value = false
  versionListLoading.value = false
  assetLoading.value = false
  issueLoading.value = false
  outlineLoading.value = false
  repositoriesLoading.value = false
  directorySearchLoading.value = false
  error.value = ''
  versionError.value = ''
  versionListError.value = ''
  versionListFailedPage.value = null
  repositoriesError.value = ''
  directorySearchError.value = ''
  outlineError.value = ''
  document.value = null
  directoryRepositories.value = []
  repositoryTocs.value = {}
  repositoryTocErrors.value = {}
  repositoryTocLoadingIds.value = []
  directorySearchQuery.value = ''
  directorySearchTerm.value = ''
  directorySearchResults.value = null
  versions.value = []
  versionPage.value = 1
  versionTotal.value = 0
  versionPickerOpen.value = false
  selected.value = null
  selectedVersionId.value = ''
  assets.value = []
  issues.value = []
  outline.value = []
  try {
    const [detail, versionResult] = await Promise.all([
      api.getDocument(documentId),
      api.getVersions(documentId, { page: 1, page_size: versionPageSize }),
    ])
    if (request !== loadRequest || documentId !== props.documentId) return
    document.value = detail
    versions.value = versionResult.items
    versionPage.value = versionResult.page
    versionTotal.value = versionResult.total
    loading.value = false
    const initial = detail.latest_successful_version
      ?? versionResult.items.find((version) => version.is_latest)
      ?? versionResult.items[0]
    await Promise.all([
      loadRepositoryDirectory(detail.repository.id, request),
      initial ? selectVersion(initial.id, request) : Promise.resolve(),
    ])
  } catch (cause) {
    if (request === loadRequest) error.value = cause instanceof ApiError ? cause.message : '文档加载失败。'
  } finally {
    if (request === loadRequest) loading.value = false
  }
}

async function loadVersionPage(page: number, force = false) {
  if (page < 1 || page > versionPageCount.value || (!force && page === versionPage.value)) return
  const request = ++versionListRequest
  const parentLoadRequest = loadRequest
  const documentId = props.documentId
  versionListLoading.value = true
  versionListError.value = ''
  versionListFailedPage.value = null
  try {
    const result = await api.getVersions(documentId, { page, page_size: versionPageSize })
    if (request !== versionListRequest || parentLoadRequest !== loadRequest || documentId !== props.documentId) return
    versions.value = result.items
    versionPage.value = result.page
    versionTotal.value = result.total
  } catch (cause) {
    if (request === versionListRequest && parentLoadRequest === loadRequest && documentId === props.documentId) {
      versionListError.value = cause instanceof ApiError ? cause.message : '版本列表加载失败。'
      versionListFailedPage.value = page
    }
  } finally {
    if (request === versionListRequest) versionListLoading.value = false
  }
}

async function selectVersion(versionId: string, parentLoadRequest = loadRequest) {
  const request = ++versionRequest
  const documentId = props.documentId
  const previousVersionId = selected.value?.id ?? ''
  selectedVersionId.value = versionId
  versionLoading.value = true
  versionError.value = ''
  assetError.value = ''
  issueError.value = ''
  outlineError.value = ''
  assetPageNumber.value = 1
  issuePageNumber.value = 1
  previewFragment.value = ''
  try {
    const detail = await api.getVersion(documentId, versionId)
    if (request !== versionRequest || parentLoadRequest !== loadRequest || documentId !== props.documentId) return
    selected.value = detail
    assets.value = []
    issues.value = []
    outline.value = []
    assetTotal.value = 0
    issueTotal.value = 0
    await Promise.all([
      loadAssetPage(1, request, documentId, versionId),
      loadIssuePage(1, request, documentId, versionId),
      loadOutline(request, documentId, versionId),
    ])
  } catch (cause) {
    if (request === versionRequest && parentLoadRequest === loadRequest && documentId === props.documentId) {
      selectedVersionId.value = previousVersionId
      versionError.value = cause instanceof ApiError ? cause.message : '版本加载失败。'
    }
  } finally {
    if (request === versionRequest) versionLoading.value = false
  }
}

async function loadAssetPage(page: number, request = versionRequest, documentId = props.documentId, versionId = selected.value?.id) {
  if (!versionId || page < 1 || page > Math.max(1, Math.ceil(assetTotal.value / 20))) return
  const listRequest = ++assetRequest
  if (request === versionRequest) {
    assetLoading.value = true
    assetError.value = ''
  }
  try {
    const result = await api.getVersionAssets(documentId, versionId, { page, page_size: 20 })
    if (listRequest !== assetRequest || request !== versionRequest || documentId !== props.documentId || versionId !== selected.value?.id) return
    assets.value = result.items
    assetTotal.value = result.total
    assetPageNumber.value = result.page
  } catch (cause) {
    if (listRequest === assetRequest && request === versionRequest) assetError.value = cause instanceof ApiError ? cause.message : '资源列表加载失败。'
  } finally {
    if (listRequest === assetRequest && request === versionRequest) assetLoading.value = false
  }
}

async function loadIssuePage(page: number, request = versionRequest, documentId = props.documentId, versionId = selected.value?.id) {
  if (!versionId || page < 1 || page > Math.max(1, Math.ceil(issueTotal.value / 20))) return
  const listRequest = ++issueRequest
  if (request === versionRequest) {
    issueLoading.value = true
    issueError.value = ''
  }
  try {
    const result = await api.getVersionIssues(documentId, versionId, { page, page_size: 20 })
    if (listRequest !== issueRequest || request !== versionRequest || documentId !== props.documentId || versionId !== selected.value?.id) return
    issues.value = result.items
    issueTotal.value = result.total
    issuePageNumber.value = result.page
  } catch (cause) {
    if (listRequest === issueRequest && request === versionRequest) issueError.value = cause instanceof ApiError ? cause.message : '问题列表加载失败。'
  } finally {
    if (listRequest === issueRequest && request === versionRequest) issueLoading.value = false
  }
}

async function loadOutline(request = versionRequest, documentId = props.documentId, versionId = selected.value?.id) {
  if (!versionId) return
  const listRequest = ++outlineRequest
  if (request === versionRequest) {
    outlineLoading.value = true
    outlineError.value = ''
  }
  try {
    const previewHtml = await api.getPreviewHtml(documentId, versionId)
    if (listRequest !== outlineRequest || request !== versionRequest || documentId !== props.documentId || versionId !== selected.value?.id) return
    outline.value = extractPreviewOutline(previewHtml)
  } catch (cause) {
    if (listRequest === outlineRequest && request === versionRequest) outlineError.value = cause instanceof ApiError ? cause.message : '文章大纲加载失败。'
  } finally {
    if (listRequest === outlineRequest && request === versionRequest) outlineLoading.value = false
  }
}

function selectVersionValue(value: unknown) {
  if (typeof value !== 'string') return
  versionPickerOpen.value = false
  if (value !== selectedVersionId.value) void selectVersion(value)
}

function versionOptionLabel(version: VersionSummary): string {
  const completeness = version.completeness === 'complete' ? '完整' : version.completeness === 'partial' ? '部分成功' : '失败'
  const issue = version.issue_count ? ` · ${version.issue_count} 个问题` : ''
  return `${formatDateTime(version.created_at)} · ${completeness}${issue}${version.is_latest ? ' · 当前' : ''}`
}

function selectHeading(heading: PreviewHeading) {
  activeTab.value = 'preview'
  previewFragment.value = heading.fragment
  mobileNavigationOpen.value = false
}

onMounted(load)
watch(() => props.documentId, () => {
  mobileNavigationOpen.value = false
  void load()
})
watch(navigationOpen, (open) => localStorage.setItem('yb_reader_navigation', open ? 'open' : 'closed'))
</script>

<template>
  <div class="yb-page">
    <PageHeader :title="document?.title ?? '文档详情'" :description="document ? `${document.repository.name} · ${document.deleted_at ? document.original_path : document.path}` : '加载文档与本地版本。'">
      <template #actions>
        <Button variant="outline" @click="router.push('/repositories/manage')"><ArrowLeft data-icon="inline-start" />返回知识库</Button>
      </template>
    </PageHeader>

    <AsyncState :loading="loading" :error="error" :empty="!document" @retry="load">
      <template v-if="document">
        <Alert v-if="document.deleted_at" variant="destructive" class="mb-4">
          <AlertTriangle /><AlertTitle>文档已在语雀删除</AlertTitle>
          <AlertDescription><p>原路径：<code class="break-all">{{ document.original_path }}</code></p><p class="mt-1">删除时间 {{ formatDateTime(document.deleted_at) }}<template v-if="document.purge_at">，预计 {{ formatDateTime(document.purge_at) }} 清理</template><template v-if="document.remaining_retention_seconds !== null">，剩余 {{ formatDuration(document.remaining_retention_seconds) }}</template>。清理前仍可预览和下载最后副本。</p></AlertDescription>
        </Alert>

        <div class="grid min-h-[calc(100svh-170px)] min-w-0 overflow-hidden rounded-lg border bg-background" :class="workspaceColumns">
          <aside v-if="navigationOpen" class="hidden min-h-0 border-r lg:block">
            <DocumentNavigation
              :active-document-id="documentId"
              :active-repository-id="document.repository.id"
              :outline="outline"
              :outline-error="outlineError"
              :outline-loading="outlineLoading"
              :repositories="directoryRepositories"
              :repositories-error="repositoriesError"
              :repositories-loading="repositoriesLoading"
              :repository-toc-errors="repositoryTocErrors"
              :repository-toc-loading-ids="repositoryTocLoadingIds"
              :repository-tocs="repositoryTocs"
              :search-error="directorySearchError"
              :search-loading="directorySearchLoading"
              :search-query="directorySearchQuery"
              :search-results="directorySearchResults"
              :search-term="directorySearchTerm"
              @clear-search="clearRepositoryDirectorySearch"
              @request-toc="loadRepositoryToc"
              @retry-outline="loadOutline()"
              @retry-repositories="loadDirectoryRepositories()"
              @search="searchRepositoryDirectory"
              @select-heading="selectHeading"
              @update-search-query="directorySearchQuery = $event"
            />
          </aside>

          <Tabs v-model="activeTab" class="flex min-h-0 min-w-0 flex-col">
            <header class="border-b">
              <div class="flex min-w-0 items-start gap-3 px-4 py-4 sm:px-5">
                <Button variant="ghost" size="icon" class="lg:hidden" title="打开阅读目录" aria-label="打开阅读目录" @click="mobileNavigationOpen = true"><PanelLeftOpen /></Button>
                <Button variant="ghost" size="icon" class="hidden lg:inline-flex" :title="navigationOpen ? '折叠阅读目录' : '展开阅读目录'" :aria-label="navigationOpen ? '折叠阅读目录' : '展开阅读目录'" @click="navigationOpen = !navigationOpen">
                  <PanelLeftClose v-if="navigationOpen" /><PanelLeftOpen v-else />
                </Button>
                <div class="min-w-0 flex-1">
                  <div class="flex flex-wrap items-center gap-2"><h1 class="text-xl font-semibold leading-7">{{ document.title }}</h1><StatusBadge v-if="selected" :status="selected.completeness" /></div>
                  <p v-if="selected" class="mt-1.5 truncate text-xs text-muted-foreground">版本保存于 {{ formatDateTime(selected.created_at) }} · 远端更新 {{ formatDateTime(selected.remote_updated_at) }} · 来源任务 {{ selected.source_job_id }}</p>
                </div>
                <div v-if="selected" class="flex shrink-0 flex-wrap justify-end gap-1.5">
                  <Button v-if="selected.downloads.raw_response" as-child variant="outline" size="icon" title="下载原始响应"><a :href="api.downloadUrl(documentId, selected.id, 'raw-response')" download aria-label="下载原始响应"><FileJson /></a></Button>
                  <Button v-if="selected.downloads.raw_body" as-child variant="outline" size="icon" title="下载原始正文"><a :href="api.downloadUrl(documentId, selected.id, 'raw-body')" download aria-label="下载原始正文"><FileText /></a></Button>
                  <Button v-if="selected.downloads.offline_html" as-child variant="outline" size="icon" title="下载离线 HTML"><a :href="api.downloadUrl(documentId, selected.id, 'offline-html')" download aria-label="下载离线 HTML"><Download /></a></Button>
                </div>
              </div>

              <div class="flex min-w-0 flex-col gap-3 border-t px-4 py-2.5 sm:flex-row sm:items-center sm:justify-between sm:px-5">
                <TabsList class="grid w-full grid-cols-3 sm:w-[390px]">
                  <TabsTrigger value="preview"><GlobeLock />正文</TabsTrigger>
                  <TabsTrigger value="assets"><Paperclip />资源 {{ selected?.asset_summary.total ?? 0 }}</TabsTrigger>
                  <TabsTrigger value="issues"><AlertTriangle />问题 {{ selected?.issue_count ?? 0 }}</TabsTrigger>
                </TabsList>
                <div class="flex min-w-0 items-center gap-2 sm:justify-end">
                  <span class="shrink-0 text-xs text-muted-foreground">版本</span>
                  <Spinner v-if="versionLoading" class="size-4" />
                  <Popover v-model:open="versionPickerOpen">
                    <PopoverTrigger as-child>
                      <Button
                        variant="outline"
                        role="combobox"
                        :aria-expanded="versionPickerOpen"
                        aria-label="选择文章版本"
                        class="min-w-0 flex-1 justify-between font-normal sm:w-[320px] sm:flex-none"
                      >
                        <span class="truncate">{{ selected ? versionOptionLabel(selected) : '选择文章版本' }}</span>
                        <ChevronsUpDown class="ml-2 shrink-0 text-muted-foreground" />
                      </Button>
                    </PopoverTrigger>
                    <PopoverContent align="end" class="w-[min(28rem,calc(100vw-2rem))] p-0">
                      <div class="flex items-center justify-between border-b px-3 py-2">
                        <div class="min-w-0">
                          <p class="text-sm font-medium">历史版本</p>
                          <p class="text-xs text-muted-foreground">第 {{ versionPage }} / {{ versionPageCount }} 页 · 共 {{ versionTotal }} 个</p>
                        </div>
                        <Spinner v-if="versionListLoading" class="size-4" />
                      </div>
                      <div v-if="versionListError" class="border-b px-3 py-3">
                        <p class="text-sm text-destructive">{{ versionListError }}</p>
                        <Button class="mt-2" variant="outline" size="sm" :disabled="versionListLoading" @click="loadVersionPage(versionListFailedPage ?? versionPage, true)">重试</Button>
                      </div>
                      <div v-if="versions.length" class="max-h-80 overflow-y-auto p-1">
                        <button
                          v-for="version in versions"
                          :key="version.id"
                          type="button"
                          class="flex min-h-10 w-full items-center gap-2 rounded-sm px-2 py-2 text-left text-sm outline-none hover:bg-accent hover:text-accent-foreground focus-visible:bg-accent focus-visible:text-accent-foreground"
                          :aria-current="version.id === selectedVersionId ? 'true' : undefined"
                          @click="selectVersionValue(version.id)"
                        >
                          <Check class="size-4 shrink-0" :class="version.id === selectedVersionId ? 'opacity-100' : 'opacity-0'" />
                          <span class="min-w-0 flex-1 break-words">{{ versionOptionLabel(version) }}</span>
                        </button>
                      </div>
                      <p v-else-if="!versionListLoading && !versionListError" class="px-3 py-6 text-center text-sm text-muted-foreground">没有可选择的版本</p>
                      <div v-if="versionTotal > versionPageSize" class="flex items-center justify-between border-t px-2 py-2">
                        <Button variant="ghost" size="icon" title="上一页版本" aria-label="上一页版本" :disabled="versionPage <= 1 || versionListLoading" @click="loadVersionPage(versionPage - 1)"><ChevronLeft /></Button>
                        <span class="text-xs tabular-nums text-muted-foreground">{{ (versionPage - 1) * versionPageSize + 1 }}-{{ Math.min(versionPage * versionPageSize, versionTotal) }}</span>
                        <Button variant="ghost" size="icon" title="下一页版本" aria-label="下一页版本" :disabled="versionPage >= versionPageCount || versionListLoading" @click="loadVersionPage(versionPage + 1)"><ChevronRight /></Button>
                      </div>
                    </PopoverContent>
                  </Popover>
                </div>
              </div>
            </header>

            <Alert v-if="versionError" variant="destructive" class="m-4"><AlertTriangle /><AlertTitle>版本未能加载</AlertTitle><AlertDescription>{{ versionError }}</AlertDescription></Alert>

            <AsyncState :loading="versionLoading && !selected" :empty="!selected" empty-title="没有可浏览版本" empty-description="失败版本不会替换最后成功版本。">
              <template v-if="selected">
                <TabsContent value="preview" class="m-0 min-h-0 flex-1 p-4 sm:p-5">
                  <Alert v-if="selected.completeness === 'partial'" class="mb-4"><AlertTriangle /><AlertTitle>此版本不完整</AlertTitle><AlertDescription>正文可用，但存在未下载附件。预览不会静默访问远程地址。</AlertDescription></Alert>
                  <div v-if="selected.preview_available" class="overflow-hidden rounded-md border bg-white">
                    <iframe :key="selected.id" :src="previewSrc" sandbox="" title="文档安全离线预览" class="block h-[calc(100svh-320px)] min-h-[560px] w-full bg-white" />
                  </div>
                  <AsyncState v-else :loading="false" empty empty-title="无法预览此版本" empty-description="可以使用上方入口下载原始响应或正文。" />
                </TabsContent>

                <TabsContent value="assets" class="m-0 min-w-0 flex-1 p-4 sm:p-5">
                  <AsyncState :loading="assetLoading" :error="assetError" :empty="!assetTotal" empty-title="该版本没有计划内附件" empty-description="此版本没有需要单独保存的附件。" @retry="loadAssetPage(assetPageNumber)">
                    <div class="yb-table-wrap rounded-lg border"><Table><TableHeader><TableRow><TableHead>附件</TableHead><TableHead>类型</TableHead><TableHead>大小</TableHead><TableHead>状态</TableHead><TableHead class="text-right">下载</TableHead></TableRow></TableHeader><TableBody>
                      <TableRow v-for="asset in assets" :key="asset.id"><TableCell><p class="font-medium">{{ asset.name }}</p><p v-if="asset.issue_code" class="mt-1 text-xs text-destructive">{{ asset.issue_code }}</p></TableCell><TableCell>{{ asset.mime_type ?? asset.type }}</TableCell><TableCell>{{ formatBytes(asset.size) }}</TableCell><TableCell><StatusBadge :status="asset.status" :label="asset.status === 'downloaded' ? '已保存' : asset.status === 'failed' ? '失败' : asset.status" /></TableCell><TableCell class="text-right"><Button v-if="asset.asset_id && asset.download_available" as-child variant="ghost" size="icon"><a :href="api.assetDownloadUrl(asset.asset_id)" download :aria-label="`下载 ${asset.name}`" :title="`下载 ${asset.name}`"><Download /></a></Button><span v-else class="text-xs text-muted-foreground">不可用</span></TableCell></TableRow>
                    </TableBody></Table></div>
                    <div v-if="assetTotal > 20" class="mt-3 flex items-center justify-end gap-2 text-sm"><Button variant="outline" size="icon" title="上一页资源" aria-label="上一页资源" :disabled="assetPageNumber <= 1" @click="loadAssetPage(assetPageNumber - 1)"><ChevronLeft /></Button><span class="min-w-16 text-center tabular-nums">{{ assetPageNumber }} / {{ Math.ceil(assetTotal / 20) }}</span><Button variant="outline" size="icon" title="下一页资源" aria-label="下一页资源" :disabled="assetPageNumber >= Math.ceil(assetTotal / 20)" @click="loadAssetPage(assetPageNumber + 1)"><ChevronRight /></Button></div>
                  </AsyncState>
                </TabsContent>

                <TabsContent value="issues" class="m-0 flex-1 p-4 sm:p-5">
                  <AsyncState :loading="issueLoading" :error="issueError" :empty="!issueTotal" empty-title="此版本没有问题" empty-description="正文与计划内附件均已处理。" @retry="loadIssuePage(issuePageNumber)">
                    <div class="flex flex-col gap-3"><Alert v-for="issue in issues" :key="issue.id" :variant="issue.level === 'error' ? 'destructive' : 'default'"><AlertTriangle /><AlertTitle>{{ issue.code }}</AlertTitle><AlertDescription><p>{{ issue.message }}</p><p class="mt-2 text-xs">尝试 {{ issue.attempt_count }} 次 · 最后发生于 {{ formatDateTime(issue.last_occurred_at) }}<span v-if="issue.safe_url"> · <a :href="issue.safe_url" target="_blank" rel="noreferrer" class="inline-flex items-center gap-1 underline">安全 URL <ExternalLink class="size-3" /></a></span></p></AlertDescription></Alert></div>
                    <div v-if="issueTotal > 20" class="mt-3 flex items-center justify-end gap-2 text-sm"><Button variant="outline" size="icon" title="上一页问题" aria-label="上一页问题" :disabled="issuePageNumber <= 1" @click="loadIssuePage(issuePageNumber - 1)"><ChevronLeft /></Button><span class="min-w-16 text-center tabular-nums">{{ issuePageNumber }} / {{ Math.ceil(issueTotal / 20) }}</span><Button variant="outline" size="icon" title="下一页问题" aria-label="下一页问题" :disabled="issuePageNumber >= Math.ceil(issueTotal / 20)" @click="loadIssuePage(issuePageNumber + 1)"><ChevronRight /></Button></div>
                  </AsyncState>
                </TabsContent>
              </template>
            </AsyncState>
          </Tabs>
        </div>

        <Sheet v-model:open="mobileNavigationOpen">
          <SheetContent side="left" class="w-[310px] max-w-[86vw] gap-0 p-0">
            <SheetHeader class="sr-only"><SheetTitle>阅读目录</SheetTitle><SheetDescription>知识库目录与文章大纲</SheetDescription></SheetHeader>
            <DocumentNavigation
              :active-document-id="documentId"
              :active-repository-id="document.repository.id"
              :outline="outline"
              :outline-error="outlineError"
              :outline-loading="outlineLoading"
              :repositories="directoryRepositories"
              :repositories-error="repositoriesError"
              :repositories-loading="repositoriesLoading"
              :repository-toc-errors="repositoryTocErrors"
              :repository-toc-loading-ids="repositoryTocLoadingIds"
              :repository-tocs="repositoryTocs"
              :search-error="directorySearchError"
              :search-loading="directorySearchLoading"
              :search-query="directorySearchQuery"
              :search-results="directorySearchResults"
              :search-term="directorySearchTerm"
              @clear-search="clearRepositoryDirectorySearch"
              @request-toc="loadRepositoryToc"
              @retry-outline="loadOutline()"
              @retry-repositories="loadDirectoryRepositories()"
              @search="searchRepositoryDirectory"
              @select-heading="selectHeading"
              @update-search-query="directorySearchQuery = $event"
            />
          </SheetContent>
        </Sheet>
      </template>
    </AsyncState>
  </div>
</template>
