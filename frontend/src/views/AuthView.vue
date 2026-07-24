<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Alert, AlertDescription, Button, Field, FieldDescription, FieldGroup, FieldLabel, Input, InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput, Spinner } from '@/components/ui'
import { DatabaseBackup, Eye, EyeOff, LockKeyhole, RefreshCw, UserRound } from 'lucide-vue-next'
import { ApiError } from '@/api'
import { useSessionStore } from '@/stores/session'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const username = ref('admin')
const password = ref('')
const confirmPassword = ref('')
const showPassword = ref(false)
const submitting = ref(false)
const checking = ref(false)
const error = ref('')
const bootstrapUnavailable = computed(() => !session.bootstrapped && Boolean(session.bootstrapError))
const isInitialize = computed(() => session.bootstrapped && !session.systemInitialized)

async function retryBootstrap() {
  checking.value = true
  error.value = ''
  try {
    await session.bootstrap(true)
    if (session.isAuthenticated) {
      const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/dashboard'
      await router.replace(redirect)
    }
  } catch {
    // The store exposes a safe error message below the form.
  } finally {
    checking.value = false
  }
}

async function submit() {
  error.value = ''
  const initializing = isInitialize.value
  if (username.value.trim().length < 3) { error.value = '管理员名称至少需要 3 个字符。'; return }
  if (password.value.length < 12) { error.value = '密码至少需要 12 个字符。'; return }
  if (isInitialize.value && password.value !== confirmPassword.value) { error.value = '两次输入的密码不一致。'; return }
  submitting.value = true
  try {
    if (initializing) await session.initialize(username.value.trim(), password.value)
    else await session.login(username.value.trim(), password.value)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : (initializing ? '/credentials' : '/dashboard')
    await router.replace(redirect)
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : '请求未完成，请检查网络后重试。'
  } finally { submitting.value = false }
}
</script>

<template>
  <main class="grid min-h-svh bg-background lg:grid-cols-[minmax(320px,0.85fr)_1.15fr]">
    <section class="hidden min-h-svh flex-col justify-between bg-foreground p-10 text-background lg:flex xl:p-14">
      <div class="flex items-center gap-3">
        <span class="flex size-10 items-center justify-center rounded-md bg-background text-foreground"><DatabaseBackup class="size-5" /></span>
        <span class="text-base font-semibold">Yuque Backup</span>
      </div>
      <div class="max-w-md">
        <h1 class="text-4xl font-semibold leading-tight">语雀备份</h1>
        <p class="mt-4 max-w-sm text-sm leading-7 text-background/70">在本地保存知识库、文档版本和可获取资源。</p>
      </div>
      <p class="text-xs text-background/55">只读备份 · 单管理员</p>
    </section>

    <section class="flex min-h-svh items-center justify-center px-5 py-10 sm:px-8">
      <div class="w-full max-w-sm">
        <div class="mb-9 flex items-center gap-3 lg:hidden">
          <span class="flex size-10 items-center justify-center rounded-md bg-primary text-primary-foreground"><DatabaseBackup class="size-5" /></span>
          <strong>Yuque Backup</strong>
        </div>
        <h2 class="text-2xl font-semibold">{{ bootstrapUnavailable ? '服务暂时不可用' : (isInitialize ? '创建本地管理员' : '管理员登录') }}</h2>
        <p class="mt-2 text-sm text-muted-foreground">{{ bootstrapUnavailable ? '未能确认系统初始化状态，请重新检查连接。' : (isInitialize ? '首次启动仅允许创建一个管理员账号。' : '使用本地管理员账号继续。') }}</p>

        <Alert v-if="error" variant="destructive" class="mt-6"><AlertDescription>{{ error }}</AlertDescription></Alert>
        <Alert v-if="bootstrapUnavailable" variant="destructive" class="mt-6">
          <AlertDescription class="flex flex-col items-start gap-3">
            <span>{{ session.bootstrapError }}</span>
            <Button variant="outline" size="sm" :disabled="checking" @click="retryBootstrap"><RefreshCw data-icon="inline-start" :class="checking ? 'animate-spin' : ''" />{{ checking ? '检查中' : '重新检查' }}</Button>
          </AlertDescription>
        </Alert>
        <form v-else class="mt-7" @submit.prevent="submit">
          <FieldGroup class="gap-5">
            <Field>
              <FieldLabel for="username">管理员名称</FieldLabel>
              <InputGroup class="h-11">
                <InputGroupAddon><UserRound /></InputGroupAddon>
                <InputGroupInput id="username" v-model="username" autocomplete="username" required />
              </InputGroup>
            </Field>
            <Field>
              <FieldLabel for="password">密码</FieldLabel>
              <InputGroup class="h-11">
                <InputGroupAddon><LockKeyhole /></InputGroupAddon>
                <InputGroupInput id="password" v-model="password" :type="showPassword ? 'text' : 'password'" :autocomplete="isInitialize ? 'new-password' : 'current-password'" required minlength="12" />
                <InputGroupAddon align="inline-end">
                  <InputGroupButton type="button" :aria-label="showPassword ? '隐藏密码' : '显示密码'" :title="showPassword ? '隐藏密码' : '显示密码'" @click="showPassword = !showPassword"><EyeOff v-if="showPassword" /><Eye v-else /></InputGroupButton>
                </InputGroupAddon>
              </InputGroup>
              <FieldDescription v-if="isInitialize">至少 12 个字符</FieldDescription>
            </Field>
            <Field v-if="isInitialize">
              <FieldLabel for="confirm-password">确认密码</FieldLabel>
              <Input id="confirm-password" v-model="confirmPassword" :type="showPassword ? 'text' : 'password'" autocomplete="new-password" required minlength="12" class="h-11" />
            </Field>
            <Button type="submit" class="h-11 w-full" :disabled="submitting">
              <Spinner v-if="submitting" data-icon="inline-start" />
              {{ submitting ? '提交中' : (isInitialize ? '完成初始化' : '登录') }}
            </Button>
          </FieldGroup>
        </form>
      </div>
    </section>
  </main>
</template>
