<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { Alert, AlertDescription, AlertTitle, Button, Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle, Spinner } from '@/components/ui'
import { CircleAlert, Inbox, RefreshCw } from 'lucide-vue-next'

const props = withDefaults(defineProps<{ loading: boolean; error?: string; empty?: boolean; emptyTitle?: string; emptyDescription?: string; loadingDelay?: number }>(), {
  loadingDelay: 180,
})
defineEmits<{ retry: [] }>()

const showLoadingIndicator = ref(false)
let loadingTimer: ReturnType<typeof setTimeout> | undefined

function updateLoadingIndicator() {
  if (loadingTimer) clearTimeout(loadingTimer)
  loadingTimer = undefined
  showLoadingIndicator.value = false
  if (!props.loading) return
  if (props.loadingDelay <= 0) {
    showLoadingIndicator.value = true
    return
  }
  loadingTimer = setTimeout(() => {
    showLoadingIndicator.value = props.loading
    loadingTimer = undefined
  }, props.loadingDelay)
}

watch(() => [props.loading, props.loadingDelay] as const, updateLoadingIndicator, { immediate: true })
onBeforeUnmount(() => { if (loadingTimer) clearTimeout(loadingTimer) })
</script>

<template>
  <div v-if="loading" class="flex min-h-24 items-center justify-center" aria-live="polite" aria-label="正在加载">
    <Spinner v-if="showLoadingIndicator" class="size-5 text-muted-foreground" />
  </div>
  <Alert v-else-if="error" variant="destructive">
    <CircleAlert />
    <AlertTitle>加载失败</AlertTitle>
    <AlertDescription class="flex flex-wrap items-center justify-between gap-3">
      <span>{{ error }}</span>
      <Button variant="outline" size="sm" @click="$emit('retry')">
        <RefreshCw data-icon="inline-start" />
        重试
      </Button>
    </AlertDescription>
  </Alert>
  <Empty v-else-if="empty">
    <EmptyHeader>
      <EmptyMedia variant="icon"><Inbox /></EmptyMedia>
      <EmptyTitle>{{ emptyTitle ?? '暂无数据' }}</EmptyTitle>
      <EmptyDescription>{{ emptyDescription ?? '当前筛选条件下没有可显示的内容。' }}</EmptyDescription>
    </EmptyHeader>
    <EmptyContent v-if="$slots.emptyAction"><slot name="emptyAction" /></EmptyContent>
  </Empty>
  <slot v-else />
</template>
