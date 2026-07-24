<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Alert, AlertDescription, AlertTitle, Button, Card, CardContent, CardDescription, CardHeader, CardTitle, Progress, Separator, toast } from '@/components/ui'
import { AlertTriangle, Archive, BookOpen, Clock3, Database, FileStack, HardDrive, ListChecks, Play, Server } from 'lucide-vue-next'
import { api, ApiError, type DashboardSummary } from '@/api'
import AsyncState from '@/components/AsyncState.vue'
import MetricCard from '@/components/MetricCard.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { formatBytes, formatDateTime, percent } from '@/utils/format'

const router = useRouter()
const summary = ref<DashboardSummary | null>(null)
const loading = ref(true)
const error = ref('')
const starting = ref(false)
let pollTimer: number | undefined

async function load(silent = false) {
  if (!silent) loading.value = true
  try { summary.value = await api.getDashboard(); error.value = '' }
  catch (cause) { error.value = cause instanceof ApiError ? cause.message : '仪表盘数据加载失败。' }
  finally { loading.value = false }
}

async function startBackup() {
  starting.value = true
  try {
    const result = await api.createJob({ type: 'all' })
    toast.success(result.merged ? '任务范围已合并到排队任务。' : '手动备份任务已创建。')
    await load(true)
    await router.push('/jobs')
  } catch (cause) { toast.error(cause instanceof ApiError ? cause.message : '任务创建失败。') }
  finally { starting.value = false }
}

onMounted(async () => {
  await load()
  pollTimer = window.setInterval(() => { if (summary.value?.current_job) void load(true) }, 5000)
})
onBeforeUnmount(() => { if (pollTimer) window.clearInterval(pollTimer) })
</script>

<template>
  <div class="yb-page">
    <PageHeader title="备份概览" description="优先查看当前任务、额度等待和需要处理的问题。">
      <template #actions>
        <Button variant="outline" @click="router.push('/jobs')"><ListChecks data-icon="inline-start" />查看任务</Button>
        <Button :disabled="starting" @click="startBackup"><Play data-icon="inline-start" />{{ starting ? '创建中' : '立即备份' }}</Button>
      </template>
    </PageHeader>

    <AsyncState :loading="loading" :error="error" @retry="load()">
      <template v-if="summary">
        <Alert v-if="summary.waiting_quota_credentials > 0 || summary.job_counts.partial > 0" class="mb-4">
          <AlertTriangle />
          <AlertTitle>备份完整性需要关注</AlertTitle>
          <AlertDescription>{{ summary.waiting_quota_credentials }} 个凭据正在等待额度，历史任务中有 {{ summary.job_counts.partial }} 次部分成功。可进入任务页查看资源级原因。</AlertDescription>
        </Alert>

        <section v-if="summary.current_job" class="mb-4" aria-labelledby="current-job-title">
          <Card>
            <CardHeader class="flex flex-row items-start justify-between gap-4">
              <div>
                <CardTitle id="current-job-title" class="text-base">当前备份任务</CardTitle>
                <CardDescription>{{ summary.current_job.trigger === 'manual' ? '手动触发' : 'Cron 触发' }} · 创建于 {{ formatDateTime(summary.current_job.created_at) }}</CardDescription>
              </div>
              <StatusBadge :status="summary.current_job.status" />
            </CardHeader>
            <CardContent class="flex flex-col gap-4">
              <div class="flex items-center justify-between text-sm"><span>总进度</span><strong>{{ percent(summary.current_job.progress) }}</strong></div>
              <Progress :model-value="summary.current_job.progress" />
              <div class="grid gap-3 text-sm sm:grid-cols-3">
                <div><span class="text-muted-foreground">文档成果</span><p class="mt-1 font-medium">{{ summary.current_job.document_succeeded }} / {{ summary.current_job.document_total }}</p></div>
                <div><span class="text-muted-foreground">资源成果</span><p class="mt-1 font-medium">{{ summary.current_job.asset_succeeded }} / {{ summary.current_job.asset_total }}</p></div>
                <div><span class="text-muted-foreground">问题</span><p class="mt-1 font-medium">{{ summary.current_job.issue_count }}</p></div>
              </div>
            </CardContent>
          </Card>
        </section>

        <section class="yb-grid-metrics" aria-label="备份统计">
          <MetricCard label="已选知识库" :value="summary.repositories" detail="纳入自动备份" :icon="BookOpen" />
          <MetricCard label="本地文档" :value="summary.documents" detail="可离线浏览" :icon="FileStack" />
          <MetricCard label="本地版本" :value="summary.versions" detail="含历史快照" :icon="Archive" />
          <MetricCard label="存储用量" :value="formatBytes(summary.storage.total_bytes)" :detail="`资源 ${formatBytes(summary.storage.asset_bytes)}`" :icon="HardDrive" />
        </section>

        <div class="mt-4 grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader><CardTitle class="text-base">调度与运行状态</CardTitle><CardDescription>全局 Cron 与 worker 心跳</CardDescription></CardHeader>
            <CardContent class="flex flex-col gap-4">
              <div class="flex items-start gap-3"><Clock3 class="mt-0.5 size-4 text-muted-foreground" /><div><p class="text-sm font-medium">{{ summary.schedule.enabled ? '调度已启用' : '调度已停用' }}</p><p class="mt-1 text-sm text-muted-foreground"><code>{{ summary.schedule.cron }}</code> · {{ summary.schedule.timezone }}</p><p class="mt-1 text-sm text-muted-foreground">下次运行 {{ formatDateTime(summary.schedule.next_run_at, summary.schedule.timezone) }}</p></div></div>
              <Separator />
              <div class="flex items-start gap-3"><AlertTriangle class="mt-0.5 size-4 text-muted-foreground" /><div><p class="text-sm font-medium">等待额度凭据</p><p class="mt-1 text-sm text-muted-foreground">{{ summary.waiting_quota_credentials }} 个</p></div></div>
              <Separator />
              <div class="flex items-start gap-3"><Server class="mt-0.5 size-4 text-muted-foreground" /><div><p class="text-sm font-medium">Worker {{ summary.worker.status === 'online' ? '在线' : '离线' }}</p><p class="mt-1 text-sm text-muted-foreground">最后心跳 {{ formatDateTime(summary.worker.last_heartbeat_at) }}</p></div></div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle class="text-base">最近结果</CardTitle><CardDescription>最后成功时间与任务分布</CardDescription></CardHeader>
            <CardContent class="flex flex-col gap-4">
              <div class="flex items-start gap-3"><Database class="mt-0.5 size-4 text-muted-foreground" /><div><p class="text-sm font-medium">最后成功</p><p class="mt-1 text-sm text-muted-foreground">{{ formatDateTime(summary.last_success_at) }}</p></div></div>
              <Separator />
              <div class="grid grid-cols-3 gap-3 text-center"><div><p class="text-xl font-semibold">{{ summary.job_counts.succeeded }}</p><p class="text-xs text-muted-foreground">成功</p></div><div><p class="text-xl font-semibold">{{ summary.job_counts.partial }}</p><p class="text-xs text-muted-foreground">部分成功</p></div><div><p class="text-xl font-semibold">{{ summary.job_counts.failed }}</p><p class="text-xs text-muted-foreground">失败</p></div></div>
            </CardContent>
          </Card>
        </div>
      </template>
    </AsyncState>
  </div>
</template>
