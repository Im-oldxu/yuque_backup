<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Field,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLabel,
  Input,
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
import { ChevronLeft, ChevronRight, Pencil, Plus, Radar, RefreshCw, Trash2 } from 'lucide-vue-next'
import { api, ApiError, API_MODE, type Credential, type Operation } from '@/api'
import AsyncState from '@/components/AsyncState.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { formatDateTime } from '@/utils/format'

const PAGE_SIZE = 10
const TERMINAL_OPERATION_STATUSES = new Set(['succeeded', 'failed', 'cancelled'])

type EditorMode = 'create' | 'edit'
type CredentialAction = 'verify' | 'discover' | 'toggle'

const credentials = ref<Credential[]>([])
const credentialPage = ref(1)
const credentialTotal = ref(0)
const operations = ref<Record<string, Operation | undefined>>({})
const pendingActions = ref<Record<string, CredentialAction | undefined>>({})
const loading = ref(true)
const error = ref('')
const editorOpen = ref(false)
const editorMode = ref<EditorMode>('create')
const editTarget = ref<Credential | null>(null)
const submitted = ref(false)
const submitting = ref(false)
const deleteTarget = ref<Credential | null>(null)
const deleteBusy = ref(false)
const pollingOperationIds = new Set<string>()
const form = reactive({ name: '', base_url: 'https://www.yuque.com', token: '' })
let disposed = false

const pageStart = computed(() => credentialTotal.value ? (credentialPage.value - 1) * PAGE_SIZE + 1 : 0)
const pageEnd = computed(() => Math.min(credentialPage.value * PAGE_SIZE, credentialTotal.value))

const nameError = computed(() => {
  if (!submitted.value) return ''
  const length = form.name.trim().length
  if (!length) return '请输入显示名称。'
  if (length > 100) return '显示名称不能超过 100 个字符。'
  return ''
})

const baseUrlError = computed(() => {
  if (!submitted.value) return ''
  try {
    const value = new URL(form.base_url.trim())
    if (
      value.protocol !== 'https:'
      || value.pathname !== '/'
      || value.search
      || value.hash
      || value.username
      || value.password
    ) return '请输入不含路径、查询或片段的 HTTPS origin。'
  }
  catch {
    return '请输入有效的 HTTPS origin。'
  }
  return ''
})

const tokenError = computed(() => {
  if (!submitted.value) return ''
  const length = form.token.trim().length
  if (editorMode.value === 'create' && !length) return '请输入 Token。'
  if (length > 2048) return 'Token 不能超过 2048 个字符。'
  return ''
})

function apiMessage(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback
}

function setPendingAction(id: string, action?: CredentialAction) {
  pendingActions.value = { ...pendingActions.value, [id]: action }
}

function setOperation(id: string, operation?: Operation) {
  operations.value = { ...operations.value, [id]: operation }
}

function currentOperation(credential: Credential): Operation | undefined {
  const operation = operations.value[credential.id]
  return operation && !TERMINAL_OPERATION_STATUSES.has(operation.status) ? operation : undefined
}

function displayedStatus(credential: Credential): string {
  return currentOperation(credential)?.status ?? credential.status
}

function isCredentialBusy(credential: Credential): boolean {
  return Boolean(pendingActions.value[credential.id] || currentOperation(credential))
}

function canEnable(credential: Credential): boolean {
  return credential.status === 'valid' || credential.status === 'disabled'
}

async function load(showLoading = true) {
  if (showLoading) loading.value = true
  try {
    const result = await api.getCredentials({ page: credentialPage.value, page_size: PAGE_SIZE })

    if (!result.items.length && result.total > 0 && credentialPage.value > 1) {
      credentialPage.value = Math.max(1, Math.ceil(result.total / PAGE_SIZE))
      await load(showLoading)
      return
    }

    credentials.value = result.items
    credentialTotal.value = result.total
    error.value = ''

    for (const credential of credentials.value) {
      if (credential.active_operation_id) void pollOperation(credential.id, credential.active_operation_id)
    }
  }
  catch (cause) {
    error.value = apiMessage(cause, '凭据列表加载失败。')
  }
  finally {
    if (showLoading) loading.value = false
  }
}

async function pollOperation(credentialId: string, operationId: string) {
  if (pollingOperationIds.has(operationId)) return
  pollingOperationIds.add(operationId)

  try {
    while (!disposed) {
      let operation: Operation
      try {
        operation = await api.getOperation(operationId)
      }
      catch (cause) {
        toast.error(apiMessage(cause, '操作进度读取失败，请稍后刷新。'))
        return
      }

      setOperation(credentialId, operation)
      if (TERMINAL_OPERATION_STATUSES.has(operation.status)) {
        await load(false)
        setOperation(credentialId)
        if (operation.status === 'failed') {
          toast.error(operation.error?.message ?? '操作执行失败，请检查凭据状态。')
        }
        return
      }

      await new Promise((resolve) => window.setTimeout(resolve, API_MODE === 'mock' ? 350 : 2500))
    }
  }
  finally {
    pollingOperationIds.delete(operationId)
  }
}

function resetEditor() {
  submitted.value = false
  editTarget.value = null
  form.name = ''
  form.base_url = 'https://www.yuque.com'
  form.token = ''
}

function openCreate() {
  resetEditor()
  editorMode.value = 'create'
  editorOpen.value = true
}

function openEdit(credential: Credential) {
  resetEditor()
  editorMode.value = 'edit'
  editTarget.value = credential
  form.name = credential.name
  form.base_url = credential.base_url
  editorOpen.value = true
}

function updateEditorOpen(open: boolean) {
  if (!open && submitting.value) return
  editorOpen.value = open
  if (!open) resetEditor()
}

async function submitCredential() {
  submitted.value = true
  if (nameError.value || baseUrlError.value || tokenError.value) return

  submitting.value = true
  try {
    if (editorMode.value === 'create') {
      const result = await api.createCredential({
        name: form.name.trim(),
        base_url: form.base_url.trim(),
        token: form.token.trim(),
      })
      credentialPage.value = Math.max(1, Math.ceil((credentialTotal.value + 1) / PAGE_SIZE))
      editorOpen.value = false
      resetEditor()
      await load(false)
      setOperation(result.credential.id, result.operation)
      toast.success('凭据已保存，正在验证。')
      void pollOperation(result.credential.id, result.operation.id)
      return
    }

    const credential = editTarget.value
    if (!credential) return
    const name = form.name.trim()
    const baseUrl = form.base_url.trim()
    const token = form.token.trim()
    const changes: Partial<{ name: string; base_url: string; token: string }> = {}

    if (name !== credential.name) changes.name = name
    if (baseUrl !== credential.base_url) changes.base_url = baseUrl
    if (token) changes.token = token

    if (!Object.keys(changes).length) {
      toast.info('没有需要保存的更改。')
      return
    }

    const requiresVerification = Boolean(changes.base_url || changes.token)
    const updated = await api.updateCredential(credential.id, changes)
    const index = credentials.value.findIndex((item) => item.id === updated.id)
    if (index >= 0) credentials.value.splice(index, 1, updated)
    editorOpen.value = false
    resetEditor()
    toast.success(requiresVerification ? '凭据已更新，请重新验证后启用。' : '凭据名称已更新。')
  }
  catch (cause) {
    toast.error(apiMessage(cause, editorMode.value === 'create' ? '凭据保存失败。' : '凭据更新失败。'))
  }
  finally {
    submitting.value = false
  }
}

async function verify(credential: Credential) {
  setPendingAction(credential.id, 'verify')
  try {
    const operation = await api.verifyCredential(credential.id)
    setOperation(credential.id, operation)
    toast.success('验证任务已创建。')
    void pollOperation(credential.id, operation.id)
  }
  catch (cause) {
    toast.error(apiMessage(cause, '无法创建验证任务。'))
  }
  finally {
    setPendingAction(credential.id)
  }
}

async function discover(credential: Credential) {
  setPendingAction(credential.id, 'discover')
  try {
    const operation = await api.discoverRepositories(credential.id)
    setOperation(credential.id, operation)
    toast.success('知识库发现任务已创建。')
    void pollOperation(credential.id, operation.id)
  }
  catch (cause) {
    toast.error(apiMessage(cause, '无法创建发现任务。'))
  }
  finally {
    setPendingAction(credential.id)
  }
}

async function toggle(credential: Credential, enabled: boolean) {
  setPendingAction(credential.id, 'toggle')
  try {
    const updated = enabled
      ? await api.enableCredential(credential.id)
      : await api.disableCredential(credential.id)
    Object.assign(credential, updated)
    toast.success(enabled ? '凭据已启用。' : '凭据已停用，已有备份不会删除。')
  }
  catch (cause) {
    toast.error(apiMessage(cause, '状态更新失败。'))
  }
  finally {
    setPendingAction(credential.id)
  }
}

async function remove() {
  if (!deleteTarget.value) return
  deleteBusy.value = true
  try {
    await api.deleteCredential(deleteTarget.value.id)
    credentialTotal.value = Math.max(0, credentialTotal.value - 1)
    credentialPage.value = Math.min(
      credentialPage.value,
      Math.max(1, Math.ceil(credentialTotal.value / PAGE_SIZE)),
    )
    deleteTarget.value = null
    await load(false)
    toast.success('凭据已删除，历史备份仍保留。')
  }
  catch (cause) {
    toast.error(apiMessage(cause, '删除失败。'))
  }
  finally {
    deleteBusy.value = false
  }
}

function changePage(page: number) {
  if (page === credentialPage.value) return
  credentialPage.value = page
  void load()
}

onMounted(() => load())
onBeforeUnmount(() => { disposed = true })
</script>

<template>
  <div class="yb-page">
    <PageHeader title="语雀凭据" description="管理访问语雀的只读 Token。完整 Token 仅在提交时发送，后续只显示脱敏值。">
      <template #actions>
        <Button @click="openCreate">
          <Plus data-icon="inline-start" />
          新增凭据
        </Button>
      </template>
    </PageHeader>

    <AsyncState
      :loading="loading"
      :error="error"
      :empty="!credentials.length && credentialTotal === 0"
      empty-title="尚未配置凭据"
      empty-description="添加语雀 API 基础域名和 Token 后，系统会通过后台队列验证凭据。"
      @retry="load"
    >
      <template #emptyAction>
        <Button @click="openCreate">
          <Plus data-icon="inline-start" />
          新增凭据
        </Button>
      </template>

      <div class="hidden overflow-hidden rounded-lg border md:block">
        <div class="overflow-x-auto">
          <Table class="min-w-[960px]">
            <TableHeader>
              <TableRow>
                <TableHead>凭据</TableHead>
                <TableHead>主体</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>额度</TableHead>
                <TableHead>知识库</TableHead>
                <TableHead>启用</TableHead>
                <TableHead class="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow v-for="credential in credentials" :key="credential.id">
                <TableCell>
                  <p class="font-medium">{{ credential.name }}</p>
                  <p class="mt-1 max-w-64 truncate text-xs text-muted-foreground">
                    {{ credential.base_url }} · {{ credential.token_masked }}
                  </p>
                </TableCell>
                <TableCell>
                  <p>{{ credential.login ?? '待识别' }}</p>
                  <p class="mt-1 text-xs text-muted-foreground">{{ credential.subject_type }}</p>
                </TableCell>
                <TableCell>
                  <StatusBadge :status="displayedStatus(credential)" />
                  <p v-if="credential.next_retry_at" class="mt-1 text-xs text-muted-foreground">
                    {{ formatDateTime(credential.next_retry_at) }} 重试
                  </p>
                  <p v-else-if="credential.last_verified_at" class="mt-1 text-xs text-muted-foreground">
                    验证于 {{ formatDateTime(credential.last_verified_at) }}
                  </p>
                </TableCell>
                <TableCell>
                  <template v-if="credential.rate_limit">
                    {{ credential.rate_limit.remaining }} / {{ credential.rate_limit.limit }}
                  </template>
                  <span v-else class="text-muted-foreground">暂无</span>
                </TableCell>
                <TableCell>{{ credential.repository_count }}</TableCell>
                <TableCell>
                  <Switch
                    :model-value="credential.enabled"
                    :disabled="pendingActions[credential.id] === 'toggle' || isCredentialBusy(credential) || (!credential.enabled && !canEnable(credential))"
                    :aria-label="`${credential.name}${credential.enabled ? '停用' : '启用'}`"
                    @update:model-value="toggle(credential, $event)"
                  />
                </TableCell>
                <TableCell>
                  <div class="flex justify-end gap-1">
                    <Button variant="ghost" size="icon" title="编辑凭据" aria-label="编辑凭据" :disabled="isCredentialBusy(credential)" @click="openEdit(credential)">
                      <Pencil />
                    </Button>
                    <Button variant="ghost" size="icon" title="重新验证" aria-label="重新验证" :disabled="isCredentialBusy(credential)" @click="verify(credential)">
                      <RefreshCw />
                    </Button>
                    <Button variant="ghost" size="icon" title="发现知识库" aria-label="发现知识库" :disabled="isCredentialBusy(credential) || credential.status !== 'valid' || !credential.enabled" @click="discover(credential)">
                      <Radar />
                    </Button>
                    <Button variant="ghost" size="icon" title="删除凭据" aria-label="删除凭据" :disabled="isCredentialBusy(credential)" @click="deleteTarget = credential">
                      <Trash2 />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
      </div>

      <div class="flex flex-col gap-3 md:hidden">
        <Card v-for="credential in credentials" :key="credential.id">
          <CardHeader>
            <div class="flex min-w-0 items-start justify-between gap-3">
              <div class="min-w-0">
                <CardTitle class="truncate text-base">{{ credential.name }}</CardTitle>
                <CardDescription class="mt-1">
                  <span class="block truncate">{{ credential.base_url }}</span>
                  <span class="mt-1 block font-mono text-xs">{{ credential.token_masked }}</span>
                </CardDescription>
              </div>
              <StatusBadge :status="displayedStatus(credential)" />
            </div>
          </CardHeader>
          <CardContent class="flex flex-col gap-3 text-sm">
            <dl class="grid grid-cols-2 gap-x-4 gap-y-3">
              <div>
                <dt class="text-xs text-muted-foreground">主体</dt>
                <dd class="mt-1 truncate">{{ credential.login ?? '待识别' }}</dd>
              </div>
              <div>
                <dt class="text-xs text-muted-foreground">知识库</dt>
                <dd class="mt-1">{{ credential.repository_count }}</dd>
              </div>
              <div>
                <dt class="text-xs text-muted-foreground">额度</dt>
                <dd class="mt-1">{{ credential.rate_limit ? `${credential.rate_limit.remaining} / ${credential.rate_limit.limit}` : '暂无' }}</dd>
              </div>
              <div>
                <dt class="text-xs text-muted-foreground">最后验证</dt>
                <dd class="mt-1">{{ formatDateTime(credential.last_verified_at) }}</dd>
              </div>
            </dl>
            <div class="flex items-center justify-between rounded-md bg-muted/50 px-3 py-2">
              <span>参与自动备份</span>
              <Switch
                :model-value="credential.enabled"
                :disabled="pendingActions[credential.id] === 'toggle' || isCredentialBusy(credential) || (!credential.enabled && !canEnable(credential))"
                :aria-label="`${credential.name}${credential.enabled ? '停用' : '启用'}`"
                @update:model-value="toggle(credential, $event)"
              />
            </div>
            <p v-if="credential.next_retry_at" class="text-xs text-muted-foreground">
              下次重试：{{ formatDateTime(credential.next_retry_at) }}
            </p>
          </CardContent>
          <CardFooter class="flex flex-wrap justify-end gap-1">
            <Button variant="ghost" size="icon" title="编辑凭据" aria-label="编辑凭据" :disabled="isCredentialBusy(credential)" @click="openEdit(credential)"><Pencil /></Button>
            <Button variant="ghost" size="icon" title="重新验证" aria-label="重新验证" :disabled="isCredentialBusy(credential)" @click="verify(credential)"><RefreshCw /></Button>
            <Button variant="ghost" size="icon" title="发现知识库" aria-label="发现知识库" :disabled="isCredentialBusy(credential) || credential.status !== 'valid' || !credential.enabled" @click="discover(credential)"><Radar /></Button>
            <Button variant="ghost" size="icon" title="删除凭据" aria-label="删除凭据" :disabled="isCredentialBusy(credential)" @click="deleteTarget = credential"><Trash2 /></Button>
          </CardFooter>
        </Card>
      </div>

      <footer v-if="credentialTotal" class="flex flex-col items-center justify-between gap-3 border-t pt-4 sm:flex-row">
        <p class="text-sm text-muted-foreground">
          显示第 {{ pageStart }}–{{ pageEnd }} 项，共 {{ credentialTotal }} 项
        </p>
        <Pagination
          v-if="credentialTotal > PAGE_SIZE"
          :page="credentialPage"
          :items-per-page="PAGE_SIZE"
          :total="credentialTotal"
          :sibling-count="1"
          show-edges
          class="mx-0 w-auto"
          @update:page="changePage"
        >
          <PaginationContent v-slot="{ items }">
            <PaginationPrevious aria-label="上一页"><ChevronLeft /></PaginationPrevious>
            <template v-for="(item, index) in items" :key="index">
              <PaginationItem v-if="item.type === 'page'" :value="item.value" :is-active="item.value === credentialPage">
                {{ item.value }}
              </PaginationItem>
              <PaginationEllipsis v-else :index="index" />
            </template>
            <PaginationNext aria-label="下一页"><ChevronRight /></PaginationNext>
          </PaginationContent>
        </Pagination>
      </footer>
    </AsyncState>

    <Dialog :open="editorOpen" @update:open="updateEditorOpen">
      <DialogContent class="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{{ editorMode === 'create' ? '新增语雀凭据' : '更新语雀凭据' }}</DialogTitle>
          <DialogDescription>
            {{ editorMode === 'create'
              ? '保存后由后台队列验证，不会在浏览器直接访问语雀。'
              : 'Token 留空会保留当前值；修改 Token 或基础域名后必须重新验证。' }}
          </DialogDescription>
        </DialogHeader>
        <form class="flex flex-col gap-6" @submit.prevent="submitCredential">
          <FieldGroup>
            <Field :data-invalid="Boolean(nameError)">
              <FieldLabel for="credential-name">显示名称</FieldLabel>
              <Input id="credential-name" v-model="form.name" maxlength="100" :aria-invalid="Boolean(nameError)" required />
              <FieldError :errors="[nameError]" />
            </Field>
            <Field :data-invalid="Boolean(baseUrlError)">
              <FieldLabel for="base-url">API 基础域名</FieldLabel>
              <Input id="base-url" v-model="form.base_url" type="url" :aria-invalid="Boolean(baseUrlError)" required />
              <FieldDescription>仅支持 HTTPS origin，不含路径、查询或片段。</FieldDescription>
              <FieldError :errors="[baseUrlError]" />
            </Field>
            <Field :data-invalid="Boolean(tokenError)">
              <FieldLabel for="credential-token">Token{{ editorMode === 'edit' ? '（可选）' : '' }}</FieldLabel>
              <Input
                id="credential-token"
                v-model="form.token"
                type="password"
                autocomplete="new-password"
                maxlength="2048"
                :aria-invalid="Boolean(tokenError)"
                :required="editorMode === 'create'"
              />
              <FieldDescription>{{ editorMode === 'edit' ? '留空表示不更换 Token。' : '完整 Token 不会在后续接口中返回。' }}</FieldDescription>
              <FieldError :errors="[tokenError]" />
            </Field>
          </FieldGroup>
          <DialogFooter>
            <Button type="button" variant="outline" :disabled="submitting" @click="updateEditorOpen(false)">取消</Button>
            <Button type="submit" :disabled="submitting">
              <Spinner v-if="submitting" data-icon="inline-start" />
              {{ submitting ? '保存中' : (editorMode === 'create' ? '保存并验证' : '保存更改') }}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>

    <ConfirmDialog
      :open="Boolean(deleteTarget)"
      title="删除凭据"
      :description="`删除“${deleteTarget?.name ?? ''}”后将停止其队列，但 ${deleteTarget?.repository_count ?? 0} 个关联知识库的已有版本和任务历史会保留。`"
      confirm-label="删除凭据"
      destructive
      :busy="deleteBusy"
      @update:open="!$event && (deleteTarget = null)"
      @confirm="remove"
    />
  </div>
</template>
