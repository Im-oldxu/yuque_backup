<script setup lang="ts">
import { Alert, AlertDescription, AlertTitle, Button, Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle, Skeleton } from '@/components/ui'
import { CircleAlert, Inbox, RefreshCw } from 'lucide-vue-next'

defineProps<{ loading: boolean; error?: string; empty?: boolean; emptyTitle?: string; emptyDescription?: string }>()
defineEmits<{ retry: [] }>()
</script>

<template>
  <div v-if="loading" class="flex flex-col gap-3" aria-live="polite" aria-label="正在加载">
    <Skeleton class="h-10 w-full" />
    <Skeleton class="h-16 w-full" />
    <Skeleton class="h-16 w-full" />
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
