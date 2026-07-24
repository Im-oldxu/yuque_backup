<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Alert, AlertDescription, AlertTitle, Button, Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, Input, InputGroup, InputGroupAddon, InputGroupInput, Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui'
import { AlertTriangle, ChevronLeft, ChevronRight, Eye, FilterX, RefreshCw, Search } from 'lucide-vue-next'
import { api, ApiError, type Paginated, type Repository, type Tombstone } from '@/api'
import AsyncState from '@/components/AsyncState.vue'
import PageHeader from '@/components/PageHeader.vue'
import { formatDateTime } from '@/utils/format'

const items = ref<Tombstone[]>([])
const repositories = ref<Repository[]>([])
const query = ref('')
const repositoryFilter = ref('all')
const deletedFrom = ref('')
const deletedTo = ref('')
const page = ref(1)
const pageSize = ref('20')
const total = ref(0)
const selected = ref<Tombstone | null>(null)
const loading = ref(true)
const error = ref('')
const repositoryLoading = ref(false)
const repositoryError = ref('')
let listRequest = 0
let repositoryRequest = 0

function toIso(value: string): string | undefined {
  if (!value) return undefined
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString()
}

async function load() {
  const request = ++listRequest
  loading.value = true
  try {
    const result = await api.getTombstones({
      page: page.value,
      page_size: Number(pageSize.value),
      q: query.value.trim() || undefined,
      repository_id: repositoryFilter.value === 'all' ? undefined : repositoryFilter.value,
      deleted_from: toIso(deletedFrom.value),
      deleted_to: toIso(deletedTo.value),
    })
    if (request !== listRequest) return
    items.value = result.items
    total.value = result.total
    page.value = result.page
    error.value = ''
  }
  catch (cause) { if (request === listRequest) error.value = cause instanceof ApiError ? cause.message : '删除记录加载失败。' }
  finally { if (request === listRequest) loading.value = false }
}

async function collectPages<T>(fetchPage: (page: number) => Promise<Paginated<T>>): Promise<T[]> {
  const first = await fetchPage(1)
  const items = [...first.items]
  for (let current = 2; items.length < first.total; current += 1) {
    const next = await fetchPage(current)
    items.push(...next.items)
    if (!next.items.length) break
  }
  return items
}

async function loadRepositories() {
  const request = ++repositoryRequest
  repositoryLoading.value = true
  repositoryError.value = ''
  try {
    const result = await collectPages((page) => api.getRepositories({ page, page_size: 100 }))
    if (request === repositoryRequest) repositories.value = result
  } catch (cause) {
    if (request === repositoryRequest) repositoryError.value = cause instanceof ApiError ? cause.message : '知识库筛选项加载失败。'
  } finally {
    if (request === repositoryRequest) repositoryLoading.value = false
  }
}

function applyFilters() {
  page.value = 1
  void load()
}

function resetFilters() {
  query.value = ''
  repositoryFilter.value = 'all'
  deletedFrom.value = ''
  deletedTo.value = ''
  pageSize.value = '20'
  applyFilters()
}

function goToPage(value: number) {
  const pageCount = Math.max(1, Math.ceil(total.value / Number(pageSize.value)))
  if (value < 1 || value > pageCount || value === page.value) return
  page.value = value
  void load()
}

onMounted(async () => {
  await Promise.all([load(), loadRepositories()])
})
onBeforeUnmount(() => {
  listRequest += 1
  repositoryRequest += 1
})
</script>

<template>
  <div class="yb-page">
    <PageHeader title="删除记录" description="内容按保留策略清理后，仅永久保留不可预览的审计墓碑。" />
    <form class="mb-4 grid gap-2 border-y bg-muted/20 px-1 py-3 sm:grid-cols-2 sm:px-3 lg:grid-cols-[minmax(220px,1fr)_220px_190px_190px_auto]" @submit.prevent="applyFilters">
      <InputGroup class="bg-background"><InputGroupAddon><Search /></InputGroupAddon><InputGroupInput v-model="query" placeholder="搜索标题或原路径" aria-label="搜索删除记录" /></InputGroup>
      <Select v-model="repositoryFilter" :disabled="repositoryLoading" @update:model-value="applyFilters"><SelectTrigger class="w-full bg-background"><SelectValue :placeholder="repositoryLoading ? '正在加载知识库' : '全部知识库'" /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="all">全部知识库</SelectItem><SelectItem v-for="repository in repositories" :key="repository.id" :value="repository.id">{{ repository.name }}</SelectItem></SelectGroup></SelectContent></Select>
      <Input v-model="deletedFrom" type="datetime-local" class="bg-background" aria-label="删除开始时间" @change="applyFilters" />
      <Input v-model="deletedTo" type="datetime-local" class="bg-background" aria-label="删除结束时间" @change="applyFilters" />
      <div class="flex gap-1"><Button type="submit" variant="outline"><Search data-icon="inline-start" />搜索</Button><Button type="button" variant="ghost" size="icon" title="重置筛选" aria-label="重置筛选" @click="resetFilters"><FilterX /></Button></div>
    </form>
    <Alert v-if="repositoryError" variant="destructive" class="mb-4"><AlertTriangle /><AlertTitle>知识库筛选项未加载</AlertTitle><AlertDescription class="flex flex-wrap items-center justify-between gap-3"><span>{{ repositoryError }}</span><Button variant="outline" size="sm" @click="loadRepositories"><RefreshCw data-icon="inline-start" />重试</Button></AlertDescription></Alert>
    <AsyncState :loading="loading" :error="error" :empty="!items.length" empty-title="暂无删除墓碑" empty-description="被删除文档完成保留期清理后会出现在这里。" @retry="load">
      <div class="yb-table-wrap rounded-lg border"><Table><TableHeader><TableRow><TableHead>文档</TableHead><TableHead>知识库</TableHead><TableHead>删除时间</TableHead><TableHead>清理时间</TableHead><TableHead class="text-right">详情</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="item in items" :key="item.id"><TableCell><p class="font-medium">{{ item.title }}</p><p class="mt-1 text-xs text-muted-foreground">{{ item.original_path }}</p></TableCell><TableCell>{{ item.repository.name }}</TableCell><TableCell>{{ formatDateTime(item.deleted_at) }}</TableCell><TableCell>{{ formatDateTime(item.purged_at) }}</TableCell><TableCell class="text-right"><Button variant="ghost" size="icon" title="查看审计详情" aria-label="查看审计详情" @click="selected = item"><Eye /></Button></TableCell></TableRow></TableBody></Table></div>
      <div class="mt-3 flex flex-col gap-2 text-sm sm:flex-row sm:items-center sm:justify-between">
        <p class="text-muted-foreground">共 {{ total }} 条删除记录</p>
        <div class="flex items-center gap-2"><Select v-model="pageSize" @update:model-value="applyFilters"><SelectTrigger class="w-[104px]"><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="20">20 条/页</SelectItem><SelectItem value="50">50 条/页</SelectItem><SelectItem value="100">100 条/页</SelectItem></SelectGroup></SelectContent></Select><Button variant="outline" size="icon" title="上一页" aria-label="上一页" :disabled="page <= 1 || loading" @click="goToPage(page - 1)"><ChevronLeft /></Button><span class="min-w-16 text-center tabular-nums">{{ page }} / {{ Math.max(1, Math.ceil(total / Number(pageSize))) }}</span><Button variant="outline" size="icon" title="下一页" aria-label="下一页" :disabled="page >= Math.max(1, Math.ceil(total / Number(pageSize))) || loading" @click="goToPage(page + 1)"><ChevronRight /></Button></div>
      </div>
    </AsyncState>

    <Dialog :open="Boolean(selected)" @update:open="!$event && (selected = null)"><DialogContent><DialogHeader><DialogTitle>{{ selected?.title }}</DialogTitle><DialogDescription>此墓碑仅用于审计，不提供正文预览或下载。</DialogDescription></DialogHeader><dl v-if="selected" class="grid gap-4 text-sm sm:grid-cols-2"><div><dt class="text-muted-foreground">语雀域名</dt><dd class="mt-1 break-all font-medium">{{ selected.base_url }}</dd></div><div><dt class="text-muted-foreground">知识库 / 文档 ID</dt><dd class="mt-1 font-medium">{{ selected.yuque_book_id }} / {{ selected.yuque_doc_id }}</dd></div><div class="sm:col-span-2"><dt class="text-muted-foreground">原路径</dt><dd class="mt-1 break-all font-medium">{{ selected.original_path }}</dd></div><div><dt class="text-muted-foreground">来源任务</dt><dd class="mt-1 font-medium">{{ selected.source_job_id }}</dd></div><div><dt class="text-muted-foreground">清理任务</dt><dd class="mt-1 font-medium">{{ selected.cleanup_job_id }}</dd></div></dl></DialogContent></Dialog>
  </div>
</template>
