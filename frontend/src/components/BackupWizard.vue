<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  Alert, AlertDescription, AlertTitle, Button, Field, FieldDescription, FieldLabel,
  FieldLegend, FieldSet, Input, Separator, Spinner, Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
  toast,
} from '@/components/ui'
import { Checkbox } from '@/components/ui/checkbox'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Stepper, StepperIndicator, StepperItem, StepperSeparator, StepperTitle } from '@/components/ui/stepper'
import { AlertTriangle, ArrowLeft, ArrowRight, Check, KeyRound, Library, Play, Save } from 'lucide-vue-next'
import {
  api, ApiError, type Credential, type JobScope, type Paginated, type QuotaEstimate, type Repository,
  type ScheduleSetting,
} from '@/api'
import StatusBadge from '@/components/StatusBadge.vue'
import { formatDateTime } from '@/utils/format'

const props = defineProps<{ mode: 'manual' | 'scheduled' }>()
const emit = defineEmits<{ completed: [] }>()

const step = ref(1)
const credentials = ref<Credential[]>([])
const repositories = ref<Repository[]>([])
const credentialId = ref('')
const selectedRepositoryIds = ref<string[]>([])
const estimate = ref<QuotaEstimate | null>(null)
const schedule = ref<ScheduleSetting | null>(null)
const scheduledTime = ref('02:00')
const loading = ref(false)
const error = ref('')
const submitting = ref(false)

const validCredentials = computed(() => credentials.value.filter((item) => item.enabled && item.status === 'valid'))
const selectedCredential = computed(() => credentials.value.find((item) => item.id === credentialId.value) ?? null)
const eligibleRepositories = computed(() => repositories.value.filter((item) => (
  item.primary_credential_id === credentialId.value && item.connection_status === 'connected'
)))
const allSelected = computed(() => (
  eligibleRepositories.value.length > 0 && selectedRepositoryIds.value.length === eligibleRepositories.value.length
))
const quotaBlocked = computed(() => estimate.value?.credentials.some((item) => item.sufficient === false) ?? false)
const currentQuota = computed(() => estimate.value?.credentials[0] ?? null)
const selectedCron = computed(() => {
  const [hourText = '', minuteText = ''] = scheduledTime.value.split(':')
  const hour = Number(hourText)
  const minute = Number(minuteText)
  if (!Number.isInteger(hour) || hour < 0 || hour > 23 || !Number.isInteger(minute) || minute < 0 || minute > 59) return null
  return `${minute} ${hour} * * *`
})
const scheduleMatchesSelection = computed(() => schedule.value?.cron === selectedCron.value)
const wizardSteps = computed(() => props.mode === 'manual'
  ? [
      { value: 1, title: '选择凭据' },
      { value: 2, title: '选择知识库' },
      { value: 3, title: '确认执行' },
    ]
  : [
      { value: 1, title: '选择凭据' },
      { value: 2, title: '选择知识库' },
      { value: 3, title: '选择执行时间' },
      { value: 4, title: '添加定时计划' },
    ])

async function collectPages<T>(fetchPage: (page: number) => Promise<Paginated<T>>): Promise<T[]> {
  const first = await fetchPage(1)
  const items = [...first.items]
  for (let page = 2; items.length < first.total; page += 1) {
    const next = await fetchPage(page)
    items.push(...next.items)
    if (!next.items.length) break
  }
  return items
}

function message(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback
}

function timeFromCron(cron: string): string {
  const [minute, hour, day, month, weekday] = cron.trim().split(/\s+/)
  if (day === '*' && month === '*' && weekday === '*' && hour && minute && /^\d+$/.test(hour) && /^\d+$/.test(minute)) {
    return `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`
  }
  return '02:00'
}

function scope(): JobScope {
  return {
    type: 'repositories',
    credential_id: credentialId.value,
    repository_ids: selectedRepositoryIds.value,
  }
}

async function loadCredentials() {
  loading.value = true
  error.value = ''
  try {
    credentials.value = await collectPages((page) => api.getCredentials({ page, page_size: 100 }))
    if (!validCredentials.value.some((item) => item.id === credentialId.value)) {
      credentialId.value = validCredentials.value[0]?.id ?? ''
    }
    if (props.mode === 'scheduled') {
      schedule.value = await api.getSchedule()
      scheduledTime.value = timeFromCron(schedule.value.cron)
    }
  } catch (cause) {
    error.value = message(cause, '备份向导加载失败。')
  } finally {
    loading.value = false
  }
}

async function loadRepositories() {
  loading.value = true
  error.value = ''
  try {
    repositories.value = await collectPages((page) => api.getRepositories({
      credential_id: credentialId.value,
      page,
      page_size: 100,
    }))
    const eligible = eligibleRepositories.value
    selectedRepositoryIds.value = props.mode === 'scheduled'
      ? eligible.filter((item) => item.selected).map((item) => item.id)
      : eligible.map((item) => item.id)
    if (!selectedRepositoryIds.value.length && eligible.length) {
      selectedRepositoryIds.value = eligible.map((item) => item.id)
    }
    step.value = 2
  } catch (cause) {
    error.value = message(cause, '知识库加载失败。')
  } finally {
    loading.value = false
  }
}

async function loadEstimate() {
  if (!selectedRepositoryIds.value.length) return
  loading.value = true
  error.value = ''
  try {
    estimate.value = await api.estimateJob(scope())
    step.value = 3
  } catch (cause) {
    error.value = message(cause, '额度预估失败。')
  } finally {
    loading.value = false
  }
}

function toggleAll(value: boolean | 'indeterminate') {
  selectedRepositoryIds.value = value === true ? eligibleRepositories.value.map((item) => item.id) : []
}

function toggleRepository(id: string, value: boolean | 'indeterminate') {
  selectedRepositoryIds.value = value === true
    ? [...new Set([...selectedRepositoryIds.value, id])]
    : selectedRepositoryIds.value.filter((item) => item !== id)
}

async function submit() {
  if (!estimate.value || !selectedRepositoryIds.value.length) return
  submitting.value = true
  try {
    if (props.mode === 'manual') {
      const result = await api.createJob(scope())
      toast.success(result.merged ? '备份范围已合并到唯一排队任务。' : '手动备份任务已创建。')
    } else {
      const selected = new Set(selectedRepositoryIds.value)
      const changed = eligibleRepositories.value.filter((item) => item.selected !== selected.has(item.id))
      await Promise.all(changed.map((item) => api.updateRepositorySelection(item.id, selected.has(item.id))))
      if (!selectedCron.value) throw new Error('invalid time')
      schedule.value = await api.updateSchedule(selectedCron.value, schedule.value?.timezone ?? 'Asia/Shanghai')
      eligibleRepositories.value.forEach((item) => { item.selected = selected.has(item.id) })
      toast.success('定时计划已添加，将在设定时间执行。')
    }
    emit('completed')
  } catch (cause) {
    toast.error(message(cause, props.mode === 'manual' ? '手动备份创建失败。' : '定时备份保存失败。'))
  } finally {
    submitting.value = false
  }
}

watch(credentialId, () => {
  if (step.value > 1) step.value = 1
  repositories.value = []
  selectedRepositoryIds.value = []
  estimate.value = null
})

watch(() => props.mode, () => {
  step.value = 1
  repositories.value = []
  selectedRepositoryIds.value = []
  estimate.value = null
  void loadCredentials()
})

onMounted(loadCredentials)
</script>

<template>
  <section class="mb-6 border-y bg-muted/15 py-5">
    <div class="mx-auto flex max-w-5xl flex-col gap-5 px-1 sm:px-4">
      <Stepper v-model="step" :class="mode === 'manual' ? 'grid grid-cols-3' : 'grid grid-cols-2 gap-y-3 sm:grid-cols-4'" linear>
        <StepperItem v-for="item in wizardSteps" :key="item.value" :step="item.value" class="min-w-0">
          <div class="flex min-w-0 items-center gap-2">
            <StepperIndicator class="size-8 shrink-0">
              <Check v-if="step > item.value" />
              <span v-else>{{ item.value }}</span>
            </StepperIndicator>
            <StepperTitle class="whitespace-nowrap text-xs sm:text-sm">{{ item.title }}</StepperTitle>
          </div>
          <StepperSeparator v-if="item.value < wizardSteps.length" class="h-px min-w-2 flex-1" />
        </StepperItem>
      </Stepper>

      <Alert v-if="error" variant="destructive">
        <AlertTriangle />
        <AlertTitle>无法继续</AlertTitle>
        <AlertDescription class="flex flex-wrap items-center justify-between gap-3">
          <span>{{ error }}</span>
          <Button variant="outline" size="sm" @click="step === 1 ? loadCredentials() : step === 2 ? loadRepositories() : loadEstimate()">重试</Button>
        </AlertDescription>
      </Alert>

      <div v-if="loading" class="flex min-h-48 items-center justify-center gap-2 text-sm text-muted-foreground">
        <Spinner />正在加载
      </div>

      <template v-else-if="step === 1">
        <FieldSet>
          <FieldLegend>有效语雀凭据</FieldLegend>
          <FieldDescription>仅可选择已验证并启用的凭据。</FieldDescription>
          <RadioGroup v-model="credentialId" class="mt-3">
            <Field v-for="credential in validCredentials" :key="credential.id" orientation="horizontal" class="grid grid-cols-[auto_minmax(0,1fr)] items-start rounded-md border p-4 sm:flex sm:items-center">
              <RadioGroupItem :id="`credential-${credential.id}`" :value="credential.id" />
              <FieldLabel :for="`credential-${credential.id}`" class="min-w-0 flex-1 cursor-pointer font-normal">
                <span class="flex flex-wrap items-center gap-2"><strong>{{ credential.name }}</strong><StatusBadge :status="credential.status" /></span>
                <span class="mt-1 block truncate text-xs text-muted-foreground">{{ credential.base_url }} · {{ credential.token_masked }}</span>
              </FieldLabel>
              <div class="col-start-2 shrink-0 text-left text-xs text-muted-foreground sm:text-right">
                <p>额度 {{ credential.rate_limit ? `${credential.rate_limit.remaining} / ${credential.rate_limit.limit}` : '未知' }}</p>
                <p v-if="credential.rate_limit">{{ formatDateTime(credential.rate_limit.observed_at) }}</p>
              </div>
            </Field>
          </RadioGroup>
        </FieldSet>
        <Alert v-if="!validCredentials.length">
          <KeyRound />
          <AlertTitle>没有有效凭据</AlertTitle>
          <AlertDescription>请先在语雀凭据中完成验证并启用凭据。</AlertDescription>
        </Alert>
      </template>

      <template v-else-if="step === 2">
        <FieldSet>
          <div class="flex flex-wrap items-end justify-between gap-3">
            <div><FieldLegend>{{ selectedCredential?.name }} 的知识库</FieldLegend><FieldDescription>仅显示该凭据当前作为主凭据且连接正常的知识库。</FieldDescription></div>
            <Field orientation="horizontal" class="w-auto">
              <Checkbox id="select-all-repositories" :model-value="allSelected" @update:model-value="toggleAll" />
              <FieldLabel for="select-all-repositories" class="cursor-pointer font-normal">全选 {{ eligibleRepositories.length }} 个</FieldLabel>
            </Field>
          </div>
          <div v-if="eligibleRepositories.length" class="yb-table-wrap mt-3 rounded-md border">
            <Table>
              <TableHeader><TableRow><TableHead class="w-12"></TableHead><TableHead>知识库</TableHead><TableHead>路径</TableHead><TableHead>本地文档</TableHead><TableHead>最近成功</TableHead></TableRow></TableHeader>
              <TableBody>
                <TableRow v-for="repository in eligibleRepositories" :key="repository.id">
                  <TableCell><Checkbox :id="`repository-${repository.id}`" :model-value="selectedRepositoryIds.includes(repository.id)" @update:model-value="toggleRepository(repository.id, $event)" /></TableCell>
                  <TableCell><FieldLabel :for="`repository-${repository.id}`" class="cursor-pointer font-medium">{{ repository.name }}</FieldLabel></TableCell>
                  <TableCell class="max-w-64 truncate text-muted-foreground">{{ repository.namespace ?? '—' }}</TableCell>
                  <TableCell>{{ repository.document_count }}</TableCell>
                  <TableCell class="text-muted-foreground">{{ formatDateTime(repository.last_success_at) }}</TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
        </FieldSet>
        <Alert v-if="!eligibleRepositories.length">
          <Library />
          <AlertTitle>没有可备份知识库</AlertTitle>
          <AlertDescription>该凭据需要先发现知识库，并被设为知识库的主凭据。</AlertDescription>
        </Alert>
      </template>

      <template v-else-if="mode === 'scheduled' && step === 3">
        <FieldSet class="mx-auto w-full max-w-xl">
          <FieldLegend>每日执行时间</FieldLegend>
          <FieldDescription>定时计划会在所选时间触发备份，保存计划时不会立即执行。</FieldDescription>
          <Field class="mt-3">
            <FieldLabel for="scheduled-time">执行时间</FieldLabel>
            <Input id="scheduled-time" v-model="scheduledTime" type="time" required />
            <FieldDescription>使用 {{ schedule?.timezone ?? 'Asia/Shanghai' }} 时区。</FieldDescription>
          </Field>
        </FieldSet>
      </template>

      <template v-else>
        <div class="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(280px,0.65fr)]">
          <div class="min-w-0">
            <h3 class="text-sm font-semibold">备份范围</h3>
            <dl class="mt-3 grid grid-cols-2 gap-4 text-sm sm:grid-cols-3">
              <div><dt class="text-muted-foreground">凭据</dt><dd class="mt-1 font-medium">{{ selectedCredential?.name }}</dd></div>
              <div><dt class="text-muted-foreground">知识库</dt><dd class="mt-1 font-medium">{{ estimate?.repository_count }}</dd></div>
              <div><dt class="text-muted-foreground">已知文档</dt><dd class="mt-1 font-medium">{{ estimate?.document_count }}</dd></div>
            </dl>
            <Separator class="my-4" />
            <h3 class="text-sm font-semibold">预计 API 额度</h3>
            <p class="mt-2 text-2xl font-semibold tabular-nums">约 {{ estimate?.estimated_api_calls }} 次</p>
            <p class="mt-1 text-xs text-muted-foreground">非精确值；远端新增、变化文档和 Table 额外分页只能在执行时确定。</p>
            <ul class="mt-3 flex flex-col gap-1 text-xs text-muted-foreground"><li v-for="item in estimate?.calculation_basis" :key="item">{{ item }}</li></ul>
          </div>

          <div class="flex flex-col gap-4 border-l-0 lg:border-l lg:pl-5">
            <div v-if="mode === 'scheduled'">
              <h3 class="text-sm font-semibold">执行计划</h3>
              <dl class="mt-3 grid grid-cols-2 gap-4 text-sm">
                <div><dt class="text-muted-foreground">执行频率</dt><dd class="mt-1 font-medium">每天 {{ scheduledTime }}</dd></div>
                <div><dt class="text-muted-foreground">时区</dt><dd class="mt-1 font-medium">{{ schedule?.timezone ?? 'Asia/Shanghai' }}</dd></div>
              </dl>
              <div v-if="scheduleMatchesSelection && schedule?.next_runs.length" class="mt-4 border-t pt-3">
                <p class="text-xs font-medium">后续三次执行时间</p>
                <ol class="mt-2 flex flex-col gap-1 text-xs text-muted-foreground">
                  <li v-for="item in schedule.next_runs" :key="item">{{ formatDateTime(item, schedule.timezone) }}</li>
                </ol>
              </div>
              <p class="mt-3 text-xs text-muted-foreground">添加后仅保存定时计划，不会立即创建备份任务。</p>
            </div>
            <Alert :variant="quotaBlocked ? 'destructive' : 'default'">
              <AlertTriangle v-if="quotaBlocked" /><Check v-else />
              <AlertTitle v-if="currentQuota?.sufficient === true">额度充足</AlertTitle>
              <AlertTitle v-else-if="currentQuota?.sufficient === false">额度不足</AlertTitle>
              <AlertTitle v-else>剩余额度未知</AlertTitle>
              <AlertDescription>
                <template v-if="currentQuota?.snapshot_fresh">最近响应剩余 {{ currentQuota.rate_limit_remaining }} / {{ currentQuota.rate_limit_limit }}，预计需要 {{ currentQuota.estimated_api_calls }} 次。</template>
                <template v-else>没有最近一小时内的有效额度快照；执行时仍会持续读取语雀响应头。</template>
                <p v-if="currentQuota?.rate_limit_observed_at" class="mt-1 text-xs">观测于 {{ formatDateTime(currentQuota.rate_limit_observed_at) }}</p>
              </AlertDescription>
            </Alert>

            <p v-if="mode === 'scheduled'" class="text-xs text-muted-foreground">仅更新当前凭据的知识库范围，其他凭据已保存的范围保持不变。</p>
          </div>
        </div>
      </template>

      <Separator />
      <div class="flex items-center justify-between gap-3">
        <Button variant="outline" :disabled="step === 1 || loading || submitting" @click="step -= 1"><ArrowLeft data-icon="inline-start" />上一步</Button>
        <Button v-if="step === 1" :disabled="!credentialId || loading" @click="loadRepositories">下一步<ArrowRight data-icon="inline-end" /></Button>
        <Button v-else-if="step === 2" :disabled="!selectedRepositoryIds.length || loading" @click="loadEstimate">下一步<ArrowRight data-icon="inline-end" /></Button>
        <Button v-else-if="mode === 'scheduled' && step === 3" :disabled="!selectedCron" @click="step = 4">下一步<ArrowRight data-icon="inline-end" /></Button>
        <Button v-else :disabled="submitting || (mode === 'manual' && quotaBlocked) || (mode === 'scheduled' && !selectedCron)" @click="submit">
          <Spinner v-if="submitting" data-icon="inline-start" />
          <Play v-else-if="mode === 'manual'" data-icon="inline-start" />
          <Save v-else data-icon="inline-start" />
          {{ submitting ? '处理中' : mode === 'manual' ? '确认并执行' : '添加定时计划' }}
        </Button>
      </div>
    </div>
  </section>
</template>
