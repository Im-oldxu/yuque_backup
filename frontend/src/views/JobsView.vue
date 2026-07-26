<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  Alert, AlertDescription, AlertTitle, Button,
  Input, Progress, ScrollArea, Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue,
  Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
  toast,
} from '@/components/ui'
import { AlertTriangle, Ban, ChevronLeft, ChevronRight, Eye, FilterX, LoaderCircle, RefreshCw } from 'lucide-vue-next'
import { api, ApiError, type BackupIssue, type BackupJob, type BackupSubtask, type Credential, type JobStatus, type Paginated, type Repository } from '@/api'
import AsyncState from '@/components/AsyncState.vue'
import BackupWizard from '@/components/BackupWizard.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { backupActivityText, backupIssueMessage } from '@/utils/backup-activity'
import { formatDateTime, percent } from '@/utils/format'

const props = defineProps<{ mode: 'manual' | 'scheduled' }>()

const jobs = ref<BackupJob[]>([])
const credentials = ref<Credential[]>([])
const repositories = ref<Repository[]>([])
const loading = ref(true)
const error = ref('')
const statusFilter = ref<JobStatus | 'all'>('all')
const triggerFilter = ref<'all' | 'manual' | 'cron'>(props.mode === 'manual' ? 'manual' : 'cron')
const credentialFilter = ref('all')
const repositoryFilter = ref('all')
const createdFrom = ref('')
const createdTo = ref('')
const page = ref(1)
const pageSize = ref('20')
const total = ref(0)
const selected = ref<BackupJob | null>(null)
const subtasks = ref<BackupSubtask[]>([])
const issues = ref<BackupIssue[]>([])
const detailLoading = ref(false)
const detailError = ref('')
const subtaskError = ref('')
const detailIssueError = ref('')
const subtaskLoading = ref(false)
const detailIssueLoading = ref(false)
const subtaskPageNumber = ref(1)
const detailIssuePageNumber = ref(1)
const subtaskTotal = ref(0)
const detailIssueTotal = ref(0)
const cancelTarget = ref<BackupJob | null>(null)
const targetLoading = ref(false)
const targetError = ref('')
let pollTimer: number | undefined
let listRequest = 0
let detailRequest = 0
let targetRequest = 0
let subtaskRequest = 0
let jobIssueRequest = 0

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / Number(pageSize.value))))
const pageStart = computed(() => total.value ? (page.value - 1) * Number(pageSize.value) + 1 : 0)
const pageEnd = computed(() => Math.min(total.value, page.value * Number(pageSize.value)))
const currentActivity = computed(() => subtasks.value.find((subtask) => subtask.activity)?.activity ?? null)

function toIso(value: string): string | undefined {
  if (!value) return undefined
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString()
}

function errorMessage(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback
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

async function load(silent = false) {
  const request = ++listRequest
  if (!silent) loading.value = true
  try {
    const result = await api.getJobs({
      page: page.value,
      page_size: Number(pageSize.value),
      status: statusFilter.value === 'all' ? undefined : statusFilter.value,
      trigger: triggerFilter.value === 'all' ? undefined : triggerFilter.value,
      credential_id: credentialFilter.value === 'all' ? undefined : credentialFilter.value,
      repository_id: repositoryFilter.value === 'all' ? undefined : repositoryFilter.value,
      created_from: toIso(createdFrom.value),
      created_to: toIso(createdTo.value),
    })
    if (request !== listRequest) return
    jobs.value = result.items
    total.value = result.total
    page.value = result.page
    error.value = ''
  } catch (cause) {
    if (request === listRequest) error.value = cause instanceof ApiError ? cause.message : '任务列表加载失败。'
  } finally {
    if (request === listRequest) loading.value = false
  }
}

function applyFilters() {
  page.value = 1
  void load()
}

function resetFilters() {
  statusFilter.value = 'all'
  triggerFilter.value = 'all'
  credentialFilter.value = 'all'
  repositoryFilter.value = 'all'
  createdFrom.value = ''
  createdTo.value = ''
  pageSize.value = '20'
  applyFilters()
}

function goToPage(value: number) {
  if (value < 1 || value > pageCount.value || value === page.value) return
  page.value = value
  void load()
}

async function openDetail(job: BackupJob, silent = false) {
  const request = ++detailRequest
  const currentSubtaskRequest = ++subtaskRequest
  const currentIssueRequest = ++jobIssueRequest
  selected.value = job
  if (!silent) {
    subtasks.value = []
    issues.value = []
    detailError.value = ''
    subtaskError.value = ''
    detailIssueError.value = ''
    subtaskLoading.value = false
    detailIssueLoading.value = false
    subtaskPageNumber.value = 1
    detailIssuePageNumber.value = 1
    subtaskTotal.value = 0
    detailIssueTotal.value = 0
    detailLoading.value = true
  }

  const [detailResult, subtaskResult, issueResult] = await Promise.allSettled([
    api.getJob(job.id),
    api.getSubtasks(job.id, { page: 1, page_size: 20 }),
    api.getJobIssues(job.id, { page: 1, page_size: 20 }),
  ])
  if (request !== detailRequest || selected.value?.id !== job.id) return

  if (detailResult.status === 'fulfilled') selected.value = detailResult.value
  else detailError.value = errorMessage(detailResult.reason, '任务详情加载失败。')
  if (subtaskResult.status === 'fulfilled' && currentSubtaskRequest === subtaskRequest) {
    subtasks.value = subtaskResult.value.items
    subtaskTotal.value = subtaskResult.value.total
  }
  else if (subtaskResult.status === 'rejected' && currentSubtaskRequest === subtaskRequest) subtaskError.value = errorMessage(subtaskResult.reason, '子任务加载失败。')
  if (issueResult.status === 'fulfilled' && currentIssueRequest === jobIssueRequest) {
    issues.value = issueResult.value.items
    detailIssueTotal.value = issueResult.value.total
  }
  else if (issueResult.status === 'rejected' && currentIssueRequest === jobIssueRequest) detailIssueError.value = errorMessage(issueResult.reason, '任务问题加载失败。')
  if (!silent) detailLoading.value = false
}

async function loadSubtaskPage(page: number) {
  if (!selected.value || page < 1 || page > Math.max(1, Math.ceil(subtaskTotal.value / 20))) return
  const request = ++subtaskRequest
  const jobId = selected.value.id
  subtaskLoading.value = true
  subtaskError.value = ''
  try {
    const result = await api.getSubtasks(jobId, { page, page_size: 20 })
    if (request !== subtaskRequest || selected.value?.id !== jobId) return
    subtasks.value = result.items
    subtaskTotal.value = result.total
    subtaskPageNumber.value = result.page
  } catch (cause) {
    if (request === subtaskRequest) subtaskError.value = errorMessage(cause, '子任务加载失败。')
  } finally {
    if (request === subtaskRequest) subtaskLoading.value = false
  }
}

async function loadDetailIssuePage(page: number) {
  if (!selected.value || page < 1 || page > Math.max(1, Math.ceil(detailIssueTotal.value / 20))) return
  const request = ++jobIssueRequest
  const jobId = selected.value.id
  detailIssueLoading.value = true
  detailIssueError.value = ''
  try {
    const result = await api.getJobIssues(jobId, { page, page_size: 20 })
    if (request !== jobIssueRequest || selected.value?.id !== jobId) return
    issues.value = result.items
    detailIssueTotal.value = result.total
    detailIssuePageNumber.value = result.page
  } catch (cause) {
    if (request === jobIssueRequest) detailIssueError.value = errorMessage(cause, '任务问题加载失败。')
  } finally {
    if (request === jobIssueRequest) detailIssueLoading.value = false
  }
}

function closeDetail() {
  detailRequest += 1
  subtaskRequest += 1
  jobIssueRequest += 1
  selected.value = null
  subtasks.value = []
  issues.value = []
  detailError.value = ''
  subtaskError.value = ''
  detailIssueError.value = ''
  detailLoading.value = false
  subtaskLoading.value = false
  detailIssueLoading.value = false
  subtaskTotal.value = 0
  detailIssueTotal.value = 0
}

function handleDetailOpen(open: boolean) {
  if (!open) closeDetail()
}

function retryDetail() {
  if (selected.value) void openDetail(selected.value)
}

async function cancelJob() {
  if (!cancelTarget.value) return
  try { await api.cancelJob(cancelTarget.value.id); toast.success('取消请求已接收，已提交成果会保留。'); cancelTarget.value = null; await load(); if (selected.value) await openDetail(selected.value) }
  catch (cause) { toast.error(cause instanceof ApiError ? cause.message : '取消失败。') }
}

async function rerun(job: BackupJob) {
  try { const result = await api.rerunJob(job.id); toast.success(result.merged ? '重新执行范围已合并。' : '已创建重新执行任务。'); await load() }
  catch (cause) { toast.error(cause instanceof ApiError ? cause.message : '重新执行失败。') }
}

async function loadTargets() {
  const request = ++targetRequest
  targetLoading.value = true
  targetError.value = ''
  const [credentialResult, repositoryResult] = await Promise.allSettled([
    collectPages((page) => api.getCredentials({ page, page_size: 100 })),
    collectPages((page) => api.getRepositories({ page, page_size: 100 })),
  ])
  if (request !== targetRequest) return

  const errors: string[] = []
  if (credentialResult.status === 'fulfilled') credentials.value = credentialResult.value
  else errors.push(errorMessage(credentialResult.reason, '凭据目标加载失败。'))
  if (repositoryResult.status === 'fulfilled') repositories.value = repositoryResult.value
  else errors.push(errorMessage(repositoryResult.reason, '知识库目标加载失败。'))
  targetError.value = errors.join(' ')
  targetLoading.value = false
}

onMounted(async () => {
  await Promise.all([load(), loadTargets()])
  pollTimer = window.setInterval(() => {
    if (!jobs.value.some((job) => ['queued','running','waiting_quota'].includes(job.status))) return
    void load(true)
    if (selected.value) void openDetail(selected.value, true)
  }, 4000)
})
watch(() => props.mode, () => {
  triggerFilter.value = props.mode === 'manual' ? 'manual' : 'cron'
  page.value = 1
  void load()
})
onBeforeUnmount(() => {
  listRequest += 1
  detailRequest += 1
  targetRequest += 1
  subtaskRequest += 1
  jobIssueRequest += 1
  if (pollTimer) window.clearInterval(pollTimer)
})
</script>

<template>
  <div class="yb-page">
    <PageHeader :title="mode === 'manual' ? '手动备份' : '定时备份'" :description="mode === 'manual' ? '选择凭据与知识库，确认预计 API 额度后执行。' : '按同一范围向导配置每日备份时间。'" />

    <BackupWizard :mode="mode" @completed="load(); loadTargets()" />

    <div class="mb-4 border-y bg-muted/20 px-1 py-3 sm:px-3">
      <div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
        <label class="grid gap-1 text-xs text-muted-foreground">状态<Select v-model="statusFilter" @update:model-value="applyFilters"><SelectTrigger class="w-full bg-background"><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="all">全部状态</SelectItem><SelectItem v-for="status in ['queued','running','waiting_quota','succeeded','partial','failed','cancelled']" :key="status" :value="status"><StatusBadge :status="status" /></SelectItem></SelectGroup></SelectContent></Select></label>
        <label class="grid gap-1 text-xs text-muted-foreground">触发方式<Select v-model="triggerFilter" @update:model-value="applyFilters"><SelectTrigger class="w-full bg-background"><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="all">全部触发方式</SelectItem><SelectItem value="manual">手动</SelectItem><SelectItem value="cron">定时</SelectItem></SelectGroup></SelectContent></Select></label>
        <label class="grid gap-1 text-xs text-muted-foreground">凭据<Select v-model="credentialFilter" @update:model-value="applyFilters"><SelectTrigger class="w-full bg-background"><SelectValue placeholder="全部凭据" /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="all">全部凭据</SelectItem><SelectItem v-for="credential in credentials" :key="credential.id" :value="credential.id">{{ credential.name }}</SelectItem></SelectGroup></SelectContent></Select></label>
        <label class="grid gap-1 text-xs text-muted-foreground">知识库<Select v-model="repositoryFilter" @update:model-value="applyFilters"><SelectTrigger class="w-full bg-background"><SelectValue placeholder="全部知识库" /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="all">全部知识库</SelectItem><SelectItem v-for="repository in repositories" :key="repository.id" :value="repository.id">{{ repository.name }}</SelectItem></SelectGroup></SelectContent></Select></label>
        <label class="grid gap-1 text-xs text-muted-foreground">开始时间<Input v-model="createdFrom" type="datetime-local" class="bg-background" @change="applyFilters" /></label>
        <label class="grid gap-1 text-xs text-muted-foreground">结束时间<Input v-model="createdTo" type="datetime-local" class="bg-background" @change="applyFilters" /></label>
      </div>
      <div class="mt-2 flex flex-wrap items-center justify-between gap-2">
        <p class="text-xs text-muted-foreground">共 {{ total }} 个任务</p>
        <div class="flex items-center gap-1"><Button variant="ghost" size="sm" @click="resetFilters"><FilterX data-icon="inline-start" />重置</Button><Button variant="outline" size="icon" title="刷新" aria-label="刷新" @click="load()"><RefreshCw /></Button></div>
      </div>
    </div>

    <Alert v-if="targetError" variant="destructive" class="mb-4"><AlertTriangle /><AlertTitle>任务目标未完整加载</AlertTitle><AlertDescription class="flex flex-wrap items-center justify-between gap-3"><span>{{ targetError }}</span><Button variant="outline" size="sm" :disabled="targetLoading" @click="loadTargets"><RefreshCw data-icon="inline-start" :class="targetLoading ? 'animate-spin' : ''" />重试</Button></AlertDescription></Alert>

    <AsyncState :loading="loading" :error="error" :empty="!jobs.length" empty-title="暂无任务" :empty-description="mode === 'manual' ? '完成上方向导后创建手动备份。' : '保存定时备份后，任务会按计划产生。'" @retry="load">
      <div class="yb-table-wrap rounded-lg border"><Table><TableHeader><TableRow><TableHead>任务</TableHead><TableHead>状态</TableHead><TableHead>进度</TableHead><TableHead>文档</TableHead><TableHead>资源</TableHead><TableHead>问题</TableHead><TableHead>时间</TableHead><TableHead class="text-right">操作</TableHead></TableRow></TableHeader><TableBody>
        <TableRow v-for="job in jobs" :key="job.id">
          <TableCell><p class="font-medium">{{ job.trigger === 'manual' ? '手动任务' : '计划任务' }}</p><p class="mt-1 text-xs text-muted-foreground">{{ job.id.slice(0, 8) }} · {{ job.scope.type }}</p></TableCell>
          <TableCell><StatusBadge :status="job.status" /><p v-if="job.next_retry_at" class="mt-1 text-xs text-muted-foreground">{{ formatDateTime(job.next_retry_at) }} 重试</p></TableCell>
          <TableCell class="min-w-32"><div class="mb-1 flex justify-between text-xs"><span>{{ percent(job.progress) }}</span></div><Progress :model-value="job.progress" /></TableCell>
          <TableCell>{{ job.document_succeeded + job.document_partial }} / {{ job.document_total }}</TableCell><TableCell>{{ job.asset_succeeded }} / {{ job.asset_total }}</TableCell><TableCell>{{ job.issue_count }}</TableCell>
          <TableCell class="text-muted-foreground">{{ formatDateTime(job.created_at) }}</TableCell>
          <TableCell><div class="flex justify-end gap-1"><Button variant="ghost" size="icon" title="查看详情" aria-label="查看详情" @click="openDetail(job)"><Eye /></Button><Button v-if="job.can_cancel" variant="ghost" size="icon" title="取消任务" aria-label="取消任务" @click="cancelTarget = job"><Ban /></Button><Button v-if="job.can_rerun" variant="ghost" size="icon" title="重新执行" aria-label="重新执行" @click="rerun(job)"><RefreshCw /></Button></div></TableCell>
        </TableRow>
      </TableBody></Table></div>
      <div class="mt-3 flex flex-col gap-2 text-sm sm:flex-row sm:items-center sm:justify-between">
        <p class="text-muted-foreground">显示 {{ pageStart }}-{{ pageEnd }}，共 {{ total }} 条</p>
        <div class="flex items-center gap-2">
          <Select v-model="pageSize" @update:model-value="applyFilters"><SelectTrigger class="w-[104px]"><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="20">20 条/页</SelectItem><SelectItem value="50">50 条/页</SelectItem><SelectItem value="100">100 条/页</SelectItem></SelectGroup></SelectContent></Select>
          <Button variant="outline" size="icon" title="上一页" aria-label="上一页" :disabled="page <= 1 || loading" @click="goToPage(page - 1)"><ChevronLeft /></Button>
          <span class="min-w-16 text-center tabular-nums">{{ page }} / {{ pageCount }}</span>
          <Button variant="outline" size="icon" title="下一页" aria-label="下一页" :disabled="page >= pageCount || loading" @click="goToPage(page + 1)"><ChevronRight /></Button>
        </div>
      </div>
    </AsyncState>

    <Sheet :open="Boolean(selected)" @update:open="handleDetailOpen">
      <SheetContent class="w-full sm:max-w-2xl">
        <SheetHeader><SheetTitle>任务详情</SheetTitle><SheetDescription v-if="selected">{{ selected.id }} · {{ formatDateTime(selected.created_at) }}</SheetDescription></SheetHeader>
        <ScrollArea class="min-h-0 flex-1 px-4 pb-6">
          <AsyncState :loading="detailLoading" :error="detailError" :empty="!selected" @retry="retryDetail">
            <template v-if="selected">
              <div class="grid grid-cols-2 gap-3 rounded-lg border p-4 text-sm"><div><span class="text-muted-foreground">状态</span><p class="mt-1"><StatusBadge :status="selected.status" /></p></div><div><span class="text-muted-foreground">总进度</span><p class="mt-1 font-medium">{{ percent(selected.progress) }}</p></div><div><span class="text-muted-foreground">文档成果</span><p class="mt-1 font-medium">{{ selected.document_succeeded + selected.document_partial }} / {{ selected.document_total }}</p></div><div><span class="text-muted-foreground">问题</span><p class="mt-1 font-medium">{{ selected.issue_count }}</p></div><div v-if="['queued','running','waiting_quota'].includes(selected.status)" class="col-span-2 border-t pt-3"><span class="text-muted-foreground">当前操作</span><p class="mt-1 flex items-start gap-2 font-medium"><LoaderCircle class="mt-0.5 size-4 shrink-0 animate-spin" />{{ backupActivityText(currentActivity) }}</p></div></div>
              <h3 class="mb-2 mt-6 text-sm font-semibold">子任务</h3>
              <AsyncState :loading="subtaskLoading" :error="subtaskError" :empty="!subtaskTotal" empty-title="没有子任务" empty-description="该总任务尚未生成凭据/知识库子任务。" @retry="loadSubtaskPage(subtaskPageNumber)"><div class="yb-table-wrap rounded-lg border"><Table><TableHeader><TableRow><TableHead>知识库</TableHead><TableHead>凭据</TableHead><TableHead>状态</TableHead><TableHead>文档</TableHead><TableHead>当前操作</TableHead><TableHead>问题</TableHead><TableHead>下次尝试</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="subtask in subtasks" :key="subtask.id"><TableCell>{{ subtask.repository.name }}</TableCell><TableCell>{{ subtask.credential.name }}</TableCell><TableCell><StatusBadge :status="subtask.status" /><p v-if="subtask.last_issue" class="mt-1 max-w-56 text-xs text-muted-foreground">{{ subtask.last_issue }}</p></TableCell><TableCell>{{ subtask.document_completed }} / {{ subtask.document_total }}</TableCell><TableCell class="max-w-72 text-xs text-muted-foreground">{{ backupActivityText(subtask.activity) }}</TableCell><TableCell>{{ subtask.issue_count }}</TableCell><TableCell class="text-muted-foreground">{{ subtask.next_retry_at ? formatDateTime(subtask.next_retry_at) : '—' }}</TableCell></TableRow></TableBody></Table></div><div v-if="subtaskTotal > 20" class="mt-3 flex items-center justify-end gap-2 text-sm"><Button variant="outline" size="icon" title="上一页子任务" aria-label="上一页子任务" :disabled="subtaskPageNumber <= 1" @click="loadSubtaskPage(subtaskPageNumber - 1)"><ChevronLeft /></Button><span class="min-w-16 text-center tabular-nums">{{ subtaskPageNumber }} / {{ Math.ceil(subtaskTotal / 20) }}</span><Button variant="outline" size="icon" title="下一页子任务" aria-label="下一页子任务" :disabled="subtaskPageNumber >= Math.ceil(subtaskTotal / 20)" @click="loadSubtaskPage(subtaskPageNumber + 1)"><ChevronRight /></Button></div></AsyncState>
              <h3 class="mb-2 mt-6 text-sm font-semibold">问题</h3>
              <AsyncState :loading="detailIssueLoading" :error="detailIssueError" :empty="!detailIssueTotal" empty-title="没有问题" empty-description="该任务未记录文档或资源异常。" @retry="loadDetailIssuePage(detailIssuePageNumber)"><div class="flex flex-col gap-3"><Alert v-for="issue in issues" :key="issue.id" :variant="issue.level === 'error' ? 'destructive' : 'default'"><AlertTriangle /><AlertTitle>{{ issue.code }}</AlertTitle><AlertDescription>{{ backupIssueMessage(issue) }}<p v-if="issue.safe_url" class="mt-1 break-all font-mono text-xs">{{ issue.safe_url }}</p><p class="mt-1 text-xs">{{ issue.document_title }} · 尝试 {{ issue.attempt_count }} 次</p></AlertDescription></Alert></div><div v-if="detailIssueTotal > 20" class="mt-3 flex items-center justify-end gap-2 text-sm"><Button variant="outline" size="icon" title="上一页问题" aria-label="上一页问题" :disabled="detailIssuePageNumber <= 1" @click="loadDetailIssuePage(detailIssuePageNumber - 1)"><ChevronLeft /></Button><span class="min-w-16 text-center tabular-nums">{{ detailIssuePageNumber }} / {{ Math.ceil(detailIssueTotal / 20) }}</span><Button variant="outline" size="icon" title="下一页问题" aria-label="下一页问题" :disabled="detailIssuePageNumber >= Math.ceil(detailIssueTotal / 20)" @click="loadDetailIssuePage(detailIssuePageNumber + 1)"><ChevronRight /></Button></div></AsyncState>
            </template>
          </AsyncState>
        </ScrollArea>
      </SheetContent>
    </Sheet>

    <ConfirmDialog :open="Boolean(cancelTarget)" title="取消备份任务" description="任务会在当前原子写入单元完成后停止；已经提交的版本和检查点会保留。" confirm-label="确认取消" destructive @update:open="!$event && (cancelTarget = null)" @confirm="cancelJob" />
  </div>
</template>
