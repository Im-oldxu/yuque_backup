<script setup lang="ts">
import { computed } from 'vue'
import { CircleAlert, CircleCheck, CircleDashed, CircleX } from 'lucide-vue-next'
import type { Completeness } from '@/api'

const props = withDefaults(defineProps<{
  completeness?: Completeness | null
  latestVersionId?: string | null
  showLabel?: boolean
}>(), {
  completeness: null,
  latestVersionId: null,
  showLabel: false,
})

const state = computed(() => {
  if (!props.latestVersionId) return { icon: CircleDashed, label: '未备份正文', tone: 'text-muted-foreground' }
  if (props.completeness === 'complete') return { icon: CircleCheck, label: '完整备份', tone: 'yb-positive' }
  if (props.completeness === 'partial') return { icon: CircleAlert, label: '部分成功', tone: 'yb-warning' }
  return { icon: CircleX, label: '备份失败', tone: 'text-destructive' }
})
</script>

<template>
  <span class="inline-flex shrink-0 items-center gap-1 whitespace-nowrap text-xs" :class="state.tone" :title="state.label">
    <component :is="state.icon" class="size-3.5" aria-hidden="true" />
    <span v-if="showLabel">{{ state.label }}</span>
    <span v-else class="sr-only">{{ state.label }}</span>
  </span>
</template>
