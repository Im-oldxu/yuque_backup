<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import {
  Alert, AlertDescription, AlertTitle, Button, Card, CardContent, CardDescription, CardHeader, CardTitle,
  Field, FieldDescription, FieldGroup, FieldLabel, Input,
  Switch, Tabs, TabsContent, TabsList, TabsTrigger, toast,
} from '@/components/ui'
import { CalendarClock, Database, KeyRound, Save, ShieldCheck } from 'lucide-vue-next'
import { api, ApiError, type RetentionSetting, type ScheduleSetting, type StorageSetting } from '@/api'
import AsyncState from '@/components/AsyncState.vue'
import PageHeader from '@/components/PageHeader.vue'
import { formatBytes, formatDateTime } from '@/utils/format'

const schedule = ref<ScheduleSetting | null>(null)
const retention = ref<RetentionSetting | null>(null)
const storage = ref<StorageSetting | null>(null)
const loading = ref(true)
const error = ref('')
const saving = ref('')
const password = reactive({ current: '', next: '', confirm: '' })
const maxAssetMb = ref(500)

async function load() {
  loading.value = true
  try {
    const [scheduleValue, retentionValue, storageValue] = await Promise.all([api.getSchedule(), api.getRetention(), api.getStorage()])
    schedule.value = scheduleValue; retention.value = retentionValue; storage.value = storageValue
    maxAssetMb.value = storageValue.max_asset_size_bytes ? Math.round(storageValue.max_asset_size_bytes / 1024 / 1024) : 500
    error.value = ''
  } catch (cause) { error.value = cause instanceof ApiError ? cause.message : '设置加载失败。' }
  finally { loading.value = false }
}

async function saveSchedule() {
  if (!schedule.value) return
  if (schedule.value.cron.trim().split(/\s+/).length !== 5) { toast.error('Cron 必须是标准五段表达式。'); return }
  saving.value = 'schedule'
  try { schedule.value = await api.updateSchedule(schedule.value.cron.trim(), schedule.value.timezone); toast.success('调度设置已保存。') }
  catch (cause) { toast.error(cause instanceof ApiError ? cause.message : '调度设置保存失败。') }
  finally { saving.value = '' }
}

async function saveRetention() {
  if (!retention.value || retention.value.retention_days < 1) { toast.error('保留天数必须为正整数。'); return }
  saving.value = 'retention'
  try { retention.value = await api.updateRetention(Math.floor(retention.value.retention_days)); toast.success('保留设置已保存，将从下次清理任务生效。') }
  catch (cause) { toast.error(cause instanceof ApiError ? cause.message : '保留设置保存失败。') }
  finally { saving.value = '' }
}

async function saveStorage() {
  if (!storage.value) return
  if (!storage.value.max_asset_size_unlimited && maxAssetMb.value < 1) { toast.error('资源上限必须为正数。'); return }
  saving.value = 'storage'
  try { storage.value = await api.updateStorageLimit(storage.value.max_asset_size_unlimited ? null : Math.floor(maxAssetMb.value * 1024 * 1024)); toast.success('单资源上限已保存。') }
  catch (cause) { toast.error(cause instanceof ApiError ? cause.message : '存储设置保存失败。') }
  finally { saving.value = '' }
}

async function changePassword() {
  if (password.next.length < 12) { toast.error('新密码至少需要 12 个字符。'); return }
  if (password.next !== password.confirm) { toast.error('两次输入的新密码不一致。'); return }
  saving.value = 'password'
  try { await api.updatePassword(password.current, password.next); password.current = ''; password.next = ''; password.confirm = ''; toast.success('密码已修改，其他会话已撤销。') }
  catch (cause) { toast.error(cause instanceof ApiError ? cause.message : '密码修改失败。') }
  finally { saving.value = '' }
}

onMounted(load)
</script>

<template>
  <div class="yb-page">
    <PageHeader title="设置" description="管理全局调度、保留策略、资源上限和本地管理员密码。" />
    <AsyncState :loading="loading" :error="error" @retry="load">
      <Tabs default-value="schedule">
        <TabsList class="grid w-full grid-cols-4 lg:w-[520px]"><TabsTrigger value="schedule"><CalendarClock />调度</TabsTrigger><TabsTrigger value="retention"><ShieldCheck />保留</TabsTrigger><TabsTrigger value="storage"><Database />存储</TabsTrigger><TabsTrigger value="account"><KeyRound />账户</TabsTrigger></TabsList>

        <TabsContent value="schedule" class="mt-4">
          <Card v-if="schedule" class="max-w-3xl"><CardHeader><CardTitle class="text-base">Cron 与时区</CardTitle><CardDescription>保存前校验标准五段 Cron，并显示后三次计划时间。</CardDescription></CardHeader><CardContent>
            <form @submit.prevent="saveSchedule"><FieldGroup><Field><FieldLabel for="cron">Cron 表达式</FieldLabel><Input id="cron" v-model="schedule.cron" class="font-mono" required /><FieldDescription>默认每天 02:00：0 2 * * *</FieldDescription></Field><Field><FieldLabel for="timezone">业务时区</FieldLabel><Input id="timezone" v-model="schedule.timezone" placeholder="Asia/Shanghai" autocomplete="off" required /><FieldDescription>填写有效的 IANA 时区名称，例如 Asia/Shanghai 或 UTC。</FieldDescription></Field><div><p class="mb-2 text-sm font-medium">后续运行时间</p><ol class="flex flex-col gap-1 text-sm text-muted-foreground"><li v-for="item in schedule.next_runs" :key="item">{{ formatDateTime(item, schedule.timezone) }}</li></ol></div><Button type="submit" class="w-fit" :disabled="saving === 'schedule'"><Save data-icon="inline-start" />{{ saving === 'schedule' ? '保存中' : '保存调度' }}</Button></FieldGroup></form>
          </CardContent></Card>
        </TabsContent>

        <TabsContent value="retention" class="mt-4">
          <Card v-if="retention" class="max-w-3xl"><CardHeader><CardTitle class="text-base">版本保留</CardTitle><CardDescription>活动文档的最新成功版本始终受保护；墓碑永久保留。</CardDescription></CardHeader><CardContent><form @submit.prevent="saveRetention"><FieldGroup><Field><FieldLabel for="retention-days">保留天数</FieldLabel><Input id="retention-days" v-model="retention.retention_days" type="number" min="1" step="1" required /><FieldDescription>历史版本按本地完成时间计算；删除文档按语雀 deleted_at 计算。</FieldDescription></Field><Button type="submit" class="w-fit" :disabled="saving === 'retention'"><Save data-icon="inline-start" />{{ saving === 'retention' ? '保存中' : '保存保留策略' }}</Button></FieldGroup></form></CardContent></Card>
        </TabsContent>

        <TabsContent value="storage" class="mt-4">
          <Card v-if="storage" class="max-w-3xl"><CardHeader><CardTitle class="text-base">内容存储</CardTitle><CardDescription>路径只读；数据库目录必须位于宿主机本地文件系统。</CardDescription></CardHeader><CardContent class="flex flex-col gap-6">
            <Alert><Database /><AlertTitle>挂载路径</AlertTitle><AlertDescription><p>数据库：<code>{{ storage.database_path }}</code></p><p class="mt-1">内容：<code>{{ storage.content_path }}</code></p></AlertDescription></Alert>
            <div class="grid gap-3 sm:grid-cols-3"><div><p class="text-xs text-muted-foreground">数据库</p><p class="mt-1 font-medium">{{ formatBytes(storage.usage.database_bytes) }}</p></div><div><p class="text-xs text-muted-foreground">版本正文</p><p class="mt-1 font-medium">{{ formatBytes(storage.usage.version_bytes) }}</p></div><div><p class="text-xs text-muted-foreground">资源</p><p class="mt-1 font-medium">{{ formatBytes(storage.usage.asset_bytes) }}</p></div></div>
            <form @submit.prevent="saveStorage"><FieldGroup><Field orientation="horizontal"><div class="flex-1"><FieldLabel for="unlimited">不限制单资源大小</FieldLabel><FieldDescription>启用后仍会进行流式处理，不将大文件整体载入内存。</FieldDescription></div><Switch id="unlimited" v-model="storage.max_asset_size_unlimited" /></Field><Field :data-disabled="storage.max_asset_size_unlimited"><FieldLabel for="asset-limit">单资源上限（MB）</FieldLabel><Input id="asset-limit" v-model="maxAssetMb" type="number" min="1" :disabled="storage.max_asset_size_unlimited" /></Field><Button type="submit" class="w-fit" :disabled="saving === 'storage'"><Save data-icon="inline-start" />{{ saving === 'storage' ? '保存中' : '保存资源上限' }}</Button></FieldGroup></form>
          </CardContent></Card>
        </TabsContent>

        <TabsContent value="account" class="mt-4">
          <Card class="max-w-3xl"><CardHeader><CardTitle class="text-base">修改管理员密码</CardTitle><CardDescription>修改成功后保留当前会话，并撤销其他已有会话。</CardDescription></CardHeader><CardContent><form @submit.prevent="changePassword"><FieldGroup><Field><FieldLabel for="current-password">当前密码</FieldLabel><Input id="current-password" v-model="password.current" type="password" autocomplete="current-password" required /></Field><Field><FieldLabel for="new-password">新密码</FieldLabel><Input id="new-password" v-model="password.next" type="password" autocomplete="new-password" minlength="12" required /></Field><Field><FieldLabel for="confirm-new-password">确认新密码</FieldLabel><Input id="confirm-new-password" v-model="password.confirm" type="password" autocomplete="new-password" minlength="12" required /></Field><Button type="submit" class="w-fit" :disabled="saving === 'password'"><KeyRound data-icon="inline-start" />{{ saving === 'password' ? '修改中' : '修改密码' }}</Button></FieldGroup></form></CardContent></Card>
        </TabsContent>
      </Tabs>
    </AsyncState>
  </div>
</template>
