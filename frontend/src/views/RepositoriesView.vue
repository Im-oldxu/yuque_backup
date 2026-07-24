<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import {
  Alert,
  AlertDescription,
  AlertTitle,
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Field,
  FieldContent,
  FieldDescription,
  FieldLabel,
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Separator,
  Spinner,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  toast,
} from '@/components/ui'
import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationNext,
  PaginationPrevious,
} from '@/components/ui/pagination'
import { AlertTriangle, BookOpen, ChevronLeft, ChevronRight, Search, X } from 'lucide-vue-next'
import { api, ApiError, type DocumentSummary, type Repository, type RepositoryCredentialSummary, type TocTree } from '@/api'
import AsyncState from '@/components/AsyncState.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import TocTreeNode from '@/components/TocTreeNode.vue'
import { cn } from '@/lib/utils'
import { formatDateTime } from '@/utils/format'

const REPOSITORY_PAGE_SIZE = 8
const DOCUMENT_PAGE_SIZE = 10

const repositories = ref<Repository[]>([])
const repositoryPage = ref(1)
const repositoryTotal = ref(0)
const repositorySearchInput = ref('')
const repositoryQuery = ref('')
const active = ref<Repository | null>(null)
const toc = ref<TocTree | null>(null)
const documents = ref<DocumentSummary[]>([])
const documentPage = ref(1)
const documentTotal = ref(0)
const documentQuery = ref('')
const loading = ref(true)
const detailLoading = ref(false)
const documentLoading = ref(false)
const selectionBusy = ref(false)
const primaryBusy = ref(false)
const error = ref('')
const detailError = ref('')
const documentError = ref('')
let detailRequest = 0
let documentRequest = 0

const repositoryPageStart = computed(() => repositoryTotal.value ? (repositoryPage.value - 1) * REPOSITORY_PAGE_SIZE + 1 : 0)
const repositoryPageEnd = computed(() => Math.min(repositoryPage.value * REPOSITORY_PAGE_SIZE, repositoryTotal.value))
const documentPageStart = computed(() => documentTotal.value ? (documentPage.value - 1) * DOCUMENT_PAGE_SIZE + 1 : 0)
const documentPageEnd = computed(() => Math.min(documentPage.value * DOCUMENT_PAGE_SIZE, documentTotal.value))
const primaryCredential = computed(() => {
  if (!active.value?.primary_credential_id) return undefined
  return active.value.credentials?.find((credential) => credential.id === active.value?.primary_credential_id)
})
const primaryNeedsAttention = computed(() => Boolean(
  active.value
  && active.value.credential_count > 1
  && (!primaryCredential.value || !credentialCanConnect(primaryCredential.value)),
))

function credentialCanConnect(credential: RepositoryCredentialSummary): boolean {
  return credential.enabled && (credential.status === 'valid' || credential.status === 'waiting_quota')
}

function credentialStateSuffix(credential: RepositoryCredentialSummary): string {
  if (!credential.enabled) return '（已停用）'
  if (credential.status === 'waiting_quota') return '（等待额度）'
  if (credential.status !== 'valid') return '（不可用）'
  return ''
}

function apiMessage(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback
}

function connectionLabel(status: Repository['connection_status']): string {
  if (status === 'connected') return '已连接'
  if (status === 'disabled') return '连接已停用'
  return '需要处理'
}

function connectionVariant(status: Repository['connection_status']): 'secondary' | 'outline' | 'destructive' {
  if (status === 'connected') return 'secondary'
  if (status === 'disabled') return 'outline'
  return 'destructive'
}

function documentTypeLabel(type: DocumentSummary['type']): string {
  const labels: Record<DocumentSummary['type'], string> = {
    Doc: '文档',
    HtmlDoc: 'HTML 文档',
    Sheet: '表格',
    Table: '数据表',
    Thread: '讨论',
    Board: '画板',
    unknown: '未知',
  }
  return labels[type]
}

function clearRepositoryDetail() {
  detailRequest += 1
  documentRequest += 1
  active.value = null
  toc.value = null
  documents.value = []
  documentTotal.value = 0
  detailError.value = ''
  documentError.value = ''
}

async function load(showLoading = true) {
  if (showLoading) loading.value = true
  try {
    const result = await api.getRepositories({
      page: repositoryPage.value,
      page_size: REPOSITORY_PAGE_SIZE,
      q: repositoryQuery.value || undefined,
    })

    if (!result.items.length && result.total > 0 && repositoryPage.value > 1) {
      repositoryPage.value = Math.max(1, Math.ceil(result.total / REPOSITORY_PAGE_SIZE))
      await load(showLoading)
      return
    }

    repositories.value = result.items
    repositoryTotal.value = result.total
    error.value = ''

    if (!repositories.value.length) {
      clearRepositoryDetail()
      return
    }

    const repository = repositories.value.find((item) => item.id === active.value?.id) ?? repositories.value[0]!
    await selectRepository(repository, active.value?.id !== repository.id)
  }
  catch (cause) {
    error.value = apiMessage(cause, '知识库加载失败。')
  }
  finally {
    if (showLoading) loading.value = false
  }
}

async function selectRepository(repository: Repository, resetDocumentPage = true) {
  const request = ++detailRequest
  const docsRequest = ++documentRequest
  active.value = repository
  if (resetDocumentPage) documentPage.value = 1
  detailLoading.value = true
  documentLoading.value = true
  detailError.value = ''
  documentError.value = ''

  try {
    const [detail, tree, documentResult] = await Promise.all([
      api.getRepository(repository.id),
      api.getToc(repository.id),
      api.getDocuments({
        repository_id: repository.id,
        q: documentQuery.value.trim() || undefined,
        page: documentPage.value,
        page_size: DOCUMENT_PAGE_SIZE,
      }),
    ])

    if (request !== detailRequest || docsRequest !== documentRequest) return
    active.value = detail
    toc.value = tree
    documents.value = documentResult.items
    documentTotal.value = documentResult.total
  }
  catch (cause) {
    if (request === detailRequest) detailError.value = apiMessage(cause, '知识库详情加载失败。')
  }
  finally {
    if (request === detailRequest) detailLoading.value = false
    if (docsRequest === documentRequest) documentLoading.value = false
  }
}

async function loadDocuments(showLoading = true) {
  if (!active.value) return
  const repositoryId = active.value.id
  const request = ++documentRequest
  if (showLoading) documentLoading.value = true
  documentError.value = ''

  try {
    const result = await api.getDocuments({
      repository_id: repositoryId,
      q: documentQuery.value.trim() || undefined,
      page: documentPage.value,
      page_size: DOCUMENT_PAGE_SIZE,
    })

    if (request !== documentRequest || active.value?.id !== repositoryId) return
    if (!result.items.length && result.total > 0 && documentPage.value > 1) {
      documentPage.value = Math.max(1, Math.ceil(result.total / DOCUMENT_PAGE_SIZE))
      await loadDocuments(showLoading)
      return
    }

    documents.value = result.items
    documentTotal.value = result.total
  }
  catch (cause) {
    if (request === documentRequest) documentError.value = apiMessage(cause, '文档列表加载失败。')
  }
  finally {
    if (showLoading && request === documentRequest) documentLoading.value = false
  }
}

function searchRepositories() {
  repositoryQuery.value = repositorySearchInput.value.trim()
  repositoryPage.value = 1
  void load()
}

function clearRepositorySearch() {
  repositorySearchInput.value = ''
  repositoryQuery.value = ''
  repositoryPage.value = 1
  void load()
}

function searchDocuments() {
  documentQuery.value = documentQuery.value.trim()
  documentPage.value = 1
  void loadDocuments()
}

function clearDocumentSearch() {
  if (!documentQuery.value) return
  documentQuery.value = ''
  documentPage.value = 1
  void loadDocuments()
}

async function toggleSelection(selected: boolean) {
  if (!active.value) return
  const repositoryId = active.value.id
  selectionBusy.value = true
  try {
    const updated = await api.updateRepositorySelection(repositoryId, selected)
    if (active.value?.id === repositoryId) Object.assign(active.value, updated)
    const listItem = repositories.value.find((item) => item.id === updated.id)
    if (listItem) Object.assign(listItem, updated)
    toast.success(selected ? '已纳入后续备份。' : '已排除，已有内容不会删除。')
  }
  catch (cause) {
    toast.error(apiMessage(cause, '选择状态更新失败。'))
  }
  finally {
    selectionBusy.value = false
  }
}

async function setPrimary(value: unknown) {
  if (!active.value || typeof value !== 'string' || value === active.value.primary_credential_id) return
  const repositoryId = active.value.id
  primaryBusy.value = true
  try {
    const updated = await api.setPrimaryCredential(repositoryId, value)
    if (active.value?.id === repositoryId) Object.assign(active.value, updated)
    const listItem = repositories.value.find((item) => item.id === updated.id)
    if (listItem) Object.assign(listItem, updated)
    toast.success('主凭据已更新。')
  }
  catch (cause) {
    toast.error(apiMessage(cause, '主凭据更新失败。'))
  }
  finally {
    primaryBusy.value = false
  }
}

function changeRepositoryPage(page: number) {
  if (page === repositoryPage.value) return
  repositoryPage.value = page
  void load()
}

function changeDocumentPage(page: number) {
  if (page === documentPage.value) return
  documentPage.value = page
  void loadDocuments()
}

onMounted(() => load())
</script>

<template>
  <div class="yb-page">
    <PageHeader title="知识库" description="按语雀官方目录浏览本地备份，并管理每个知识库的备份范围与主凭据。" />

    <AsyncState
      :loading="loading"
      :error="error"
      :empty="!repositories.length && !repositoryQuery"
      empty-title="暂无知识库"
      empty-description="先在凭据页验证并启用 Token，然后执行知识库发现。"
      @retry="load"
    >
      <div class="grid min-h-[calc(100svh-160px)] overflow-hidden rounded-lg border lg:grid-cols-[264px_minmax(0,1fr)]">
        <aside class="flex min-h-0 flex-col border-b bg-muted/20 lg:border-r lg:border-b-0">
          <div class="flex flex-col gap-3 p-3">
            <form class="flex gap-2" role="search" @submit.prevent="searchRepositories">
              <InputGroup>
                <InputGroupAddon><Search /></InputGroupAddon>
                <InputGroupInput v-model="repositorySearchInput" maxlength="200" placeholder="搜索知识库" aria-label="搜索知识库名称或路径" />
                <InputGroupAddon v-if="repositorySearchInput" align="inline-end">
                  <InputGroupButton type="button" size="icon-xs" title="清除知识库搜索" aria-label="清除知识库搜索" @click="clearRepositorySearch"><X /></InputGroupButton>
                </InputGroupAddon>
              </InputGroup>
              <Button type="submit" variant="outline" size="icon" title="搜索知识库" aria-label="搜索知识库"><Search /></Button>
            </form>
            <p class="text-xs text-muted-foreground">
              <template v-if="repositoryTotal">第 {{ repositoryPageStart }}–{{ repositoryPageEnd }} 个，共 {{ repositoryTotal }} 个</template>
              <template v-else>没有匹配的知识库</template>
            </p>
          </div>
          <Separator />

          <AsyncState
            :loading="false"
            :empty="!repositories.length"
            :empty-title="repositoryQuery ? '没有匹配的知识库' : '暂无知识库'"
            :empty-description="repositoryQuery ? '请调整名称或路径关键词。' : '执行知识库发现后会显示在这里。'"
          >
            <template #emptyAction>
              <Button v-if="repositoryQuery" variant="outline" size="sm" @click="clearRepositorySearch">清除搜索</Button>
            </template>
            <nav class="flex flex-col gap-1 p-2" aria-label="知识库列表">
              <button
                v-for="repository in repositories"
                :key="repository.id"
                type="button"
                :class="cn(
                  'flex min-h-14 w-full items-center gap-3 rounded-md px-3 text-left hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  active?.id === repository.id && 'bg-accent text-accent-foreground',
                )"
                @click="selectRepository(repository)"
              >
                <BookOpen class="size-4 shrink-0" />
                <span class="min-w-0 flex-1">
                  <strong class="block truncate text-sm font-medium">{{ repository.name }}</strong>
                  <span class="mt-1 block truncate text-xs text-muted-foreground">
                    {{ repository.document_count }} 篇 · {{ repository.selected ? '已选择' : '已排除' }}
                  </span>
                </span>
                <span v-if="repository.connection_status !== 'connected'" class="size-2 shrink-0 rounded-full bg-destructive" :title="connectionLabel(repository.connection_status)" />
              </button>
            </nav>
          </AsyncState>

          <div v-if="repositoryTotal > REPOSITORY_PAGE_SIZE" class="mt-auto border-t p-2">
            <Pagination
              :page="repositoryPage"
              :items-per-page="REPOSITORY_PAGE_SIZE"
              :total="repositoryTotal"
              :sibling-count="0"
              @update:page="changeRepositoryPage"
            >
              <PaginationContent v-slot="{ items }">
                <PaginationPrevious aria-label="上一页"><ChevronLeft /></PaginationPrevious>
                <template v-for="(item, index) in items" :key="index">
                  <PaginationItem v-if="item.type === 'page'" :value="item.value" :is-active="item.value === repositoryPage">{{ item.value }}</PaginationItem>
                  <PaginationEllipsis v-else :index="index" />
                </template>
                <PaginationNext aria-label="下一页"><ChevronRight /></PaginationNext>
              </PaginationContent>
            </Pagination>
          </div>
        </aside>

        <section class="min-w-0">
          <AsyncState
            :loading="detailLoading"
            :error="detailError"
            :empty="!active"
            empty-title="请选择知识库"
            empty-description="从左侧列表选择一个知识库以查看官方目录和本地文档。"
            @retry="active && selectRepository(active, false)"
          >
            <template v-if="active">
              <header class="flex flex-col gap-4 border-b p-4 xl:flex-row xl:items-start xl:justify-between">
                <div class="min-w-0">
                  <div class="flex flex-wrap items-center gap-2">
                    <h2 class="text-lg font-semibold">{{ active.name }}</h2>
                    <Badge :variant="connectionVariant(active.connection_status)">{{ connectionLabel(active.connection_status) }}</Badge>
                    <Badge variant="outline">{{ active.document_count }} 篇文档</Badge>
                  </div>
                  <p class="mt-1 truncate text-sm text-muted-foreground">{{ active.namespace ?? active.base_url }}</p>
                  <p class="mt-1 text-xs text-muted-foreground">最近成功：{{ formatDateTime(active.last_success_at) }}</p>
                </div>
                <Field orientation="horizontal" class="w-auto shrink-0">
                  <FieldLabel :for="`repository-selection-${active.id}`">纳入备份</FieldLabel>
                  <Switch
                    :id="`repository-selection-${active.id}`"
                    :model-value="active.selected"
                    :disabled="selectionBusy"
                    :aria-busy="selectionBusy"
                    @update:model-value="toggleSelection"
                  />
                </Field>
              </header>

              <div v-if="active.credential_count > 1" class="border-b p-4">
                <Alert v-if="primaryNeedsAttention" :variant="primaryCredential ? 'destructive' : 'default'">
                  <AlertTriangle />
                  <AlertTitle>{{ primaryCredential ? '当前主凭据不可用' : '需要指定主凭据' }}</AlertTitle>
                  <AlertDescription class="mt-2 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                    <span>{{ primaryCredential ? '请选择一个可连接且已启用的备用凭据；系统不会自动切换。' : '多个凭据发现了同一知识库，备份前必须明确选择一个主凭据。' }}</span>
                    <Select :model-value="active.primary_credential_id ?? undefined" :disabled="primaryBusy" @update:model-value="setPrimary">
                      <SelectTrigger class="w-full sm:w-60" aria-label="选择主凭据"><SelectValue placeholder="选择主凭据" /></SelectTrigger>
                      <SelectContent>
                        <SelectGroup>
                          <SelectItem
                            v-for="credential in active.credentials ?? []"
                            :key="credential.id"
                            :value="credential.id"
                            :disabled="!credentialCanConnect(credential)"
                          >
                            {{ credential.name }}{{ credentialStateSuffix(credential) }}
                          </SelectItem>
                        </SelectGroup>
                      </SelectContent>
                    </Select>
                  </AlertDescription>
                </Alert>

                <Field v-else orientation="responsive">
                  <FieldContent>
                    <FieldLabel for="primary-credential">主凭据</FieldLabel>
                    <FieldDescription>备用凭据不会自动发出请求，切换不会改变已有文档和版本身份。</FieldDescription>
                  </FieldContent>
                  <Select :model-value="active.primary_credential_id ?? undefined" :disabled="primaryBusy" @update:model-value="setPrimary">
                    <SelectTrigger id="primary-credential" class="w-full sm:w-60"><SelectValue placeholder="选择主凭据" /></SelectTrigger>
                    <SelectContent>
                      <SelectGroup>
                        <SelectItem
                          v-for="credential in active.credentials ?? []"
                          :key="credential.id"
                          :value="credential.id"
                          :disabled="!credentialCanConnect(credential)"
                        >
                          {{ credential.name }}{{ credentialStateSuffix(credential) }}
                        </SelectItem>
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                </Field>
              </div>

              <div class="grid min-w-0 lg:grid-cols-[216px_minmax(0,1fr)]">
                <aside class="border-b p-3 lg:border-r lg:border-b-0">
                  <div class="mb-3 flex flex-col gap-1 px-2">
                    <p class="text-xs font-medium text-muted-foreground">官方目录</p>
                    <p v-if="toc" class="text-xs text-muted-foreground">{{ formatDateTime(toc.updated_at) }}</p>
                  </div>
                  <AsyncState
                    :loading="false"
                    :empty="!toc?.items.length"
                    empty-title="目录为空"
                    empty-description="当前备份尚未包含官方目录节点。"
                  >
                    <ul class="flex flex-col gap-0.5">
                      <TocTreeNode v-for="node in toc?.items ?? []" :key="node.id" :node="node" />
                    </ul>
                  </AsyncState>
                </aside>

                <div class="min-w-0 p-4">
                  <div class="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <form class="flex min-w-0 flex-1 gap-2" role="search" @submit.prevent="searchDocuments">
                      <InputGroup>
                        <InputGroupAddon><Search /></InputGroupAddon>
                        <InputGroupInput v-model="documentQuery" maxlength="200" placeholder="搜索标题或路径" aria-label="搜索文档标题或路径" />
                        <InputGroupAddon v-if="documentQuery" align="inline-end">
                          <InputGroupButton type="button" size="icon-xs" title="清除文档搜索" aria-label="清除文档搜索" @click="clearDocumentSearch"><X /></InputGroupButton>
                        </InputGroupAddon>
                      </InputGroup>
                      <Button type="submit" variant="outline" :disabled="documentLoading">
                        <Spinner v-if="documentLoading" data-icon="inline-start" />
                        <Search v-else data-icon="inline-start" />
                        搜索
                      </Button>
                    </form>
                    <p v-if="documentTotal" class="shrink-0 text-sm text-muted-foreground">
                      第 {{ documentPageStart }}–{{ documentPageEnd }} 篇，共 {{ documentTotal }} 篇
                    </p>
                  </div>

                  <AsyncState
                    :loading="documentLoading"
                    :error="documentError"
                    :empty="!documents.length"
                    :empty-title="documentQuery ? '没有匹配的文档' : '暂无本地文档'"
                    :empty-description="documentQuery ? '搜索仅匹配标题、slug 和路径，不搜索正文。' : '完成首次备份后，文档会按官方目录显示。'"
                    @retry="loadDocuments"
                  >
                    <div class="hidden overflow-hidden rounded-lg border md:block">
                      <div class="overflow-x-auto">
                        <Table class="min-w-[620px]">
                          <TableHeader>
                            <TableRow>
                              <TableHead>文档</TableHead>
                              <TableHead>类型</TableHead>
                              <TableHead>完整性</TableHead>
                              <TableHead>更新时间</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            <TableRow v-for="document in documents" :key="document.id">
                              <TableCell>
                                <RouterLink :to="`/documents/${document.id}`" class="font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{{ document.title }}</RouterLink>
                                <p class="mt-1 max-w-md truncate text-xs text-muted-foreground">{{ document.path }}</p>
                              </TableCell>
                              <TableCell><Badge variant="outline">{{ documentTypeLabel(document.type) }}</Badge></TableCell>
                              <TableCell>
                                <div class="flex flex-wrap gap-1">
                                  <StatusBadge v-if="document.latest_version_completeness" :status="document.latest_version_completeness" />
                                  <Badge v-else variant="outline">暂无版本</Badge>
                                  <Badge v-if="document.deleted_at" variant="destructive">已删除</Badge>
                                </div>
                              </TableCell>
                              <TableCell class="text-muted-foreground">{{ formatDateTime(document.updated_at) }}</TableCell>
                            </TableRow>
                          </TableBody>
                        </Table>
                      </div>
                    </div>

                    <div class="flex flex-col gap-3 md:hidden">
                      <Card v-for="document in documents" :key="document.id">
                        <CardHeader>
                          <div class="flex min-w-0 items-start justify-between gap-3">
                            <div class="min-w-0">
                              <CardTitle class="truncate text-base">
                                <RouterLink :to="`/documents/${document.id}`" class="hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{{ document.title }}</RouterLink>
                              </CardTitle>
                              <CardDescription class="mt-1 truncate">{{ document.path }}</CardDescription>
                            </div>
                            <Badge variant="outline">{{ documentTypeLabel(document.type) }}</Badge>
                          </div>
                        </CardHeader>
                        <CardContent class="flex flex-wrap items-center justify-between gap-2">
                          <div class="flex flex-wrap gap-1">
                            <StatusBadge v-if="document.latest_version_completeness" :status="document.latest_version_completeness" />
                            <Badge v-else variant="outline">暂无版本</Badge>
                            <Badge v-if="document.deleted_at" variant="destructive">已删除</Badge>
                          </div>
                          <span class="text-xs text-muted-foreground">{{ formatDateTime(document.updated_at) }}</span>
                        </CardContent>
                      </Card>
                    </div>

                    <div v-if="documentTotal > DOCUMENT_PAGE_SIZE" class="border-t pt-4">
                      <Pagination
                        :page="documentPage"
                        :items-per-page="DOCUMENT_PAGE_SIZE"
                        :total="documentTotal"
                        :sibling-count="1"
                        show-edges
                        @update:page="changeDocumentPage"
                      >
                        <PaginationContent v-slot="{ items }">
                          <PaginationPrevious aria-label="上一页"><ChevronLeft /></PaginationPrevious>
                          <template v-for="(item, index) in items" :key="index">
                            <PaginationItem v-if="item.type === 'page'" :value="item.value" :is-active="item.value === documentPage">{{ item.value }}</PaginationItem>
                            <PaginationEllipsis v-else :index="index" />
                          </template>
                          <PaginationNext aria-label="下一页"><ChevronRight /></PaginationNext>
                        </PaginationContent>
                      </Pagination>
                    </div>
                  </AsyncState>
                </div>
              </div>
            </template>
          </AsyncState>
        </section>
      </div>
    </AsyncState>
  </div>
</template>
