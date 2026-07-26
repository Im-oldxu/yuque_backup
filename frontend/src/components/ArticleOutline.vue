<script setup lang="ts">
import { Button, Spinner } from '@/components/ui'
import { RefreshCw } from 'lucide-vue-next'
import type { PreviewHeading } from '@/utils/preview-outline'

defineProps<{
  activeHeadingId?: string
  error?: string
  headings: PreviewHeading[]
  loading?: boolean
}>()

const emit = defineEmits<{
  retry: []
  select: [heading: PreviewHeading]
}>()
</script>

<template>
  <div v-if="loading" class="flex items-center justify-center gap-2 px-3 py-8 text-sm text-muted-foreground">
    <Spinner />正在读取文章大纲
  </div>
  <div v-else-if="error" class="px-3 py-8 text-center">
    <p class="text-sm text-destructive">{{ error }}</p>
    <Button variant="outline" size="sm" class="mt-3" @click="emit('retry')"><RefreshCw data-icon="inline-start" />重试</Button>
  </div>
  <nav v-else-if="headings.length" aria-label="文章大纲" class="flex flex-col gap-0.5">
    <button
      v-for="heading in headings"
      :key="`${heading.id}-${heading.level}`"
      type="button"
      class="min-h-8 w-full truncate rounded-md border-l-2 border-transparent py-1.5 pr-2 text-left text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      :class="[
        heading.level === 1 ? 'font-medium text-foreground' : '',
        heading.id === activeHeadingId ? 'border-foreground bg-accent/60 font-medium text-foreground' : '',
      ]"
      :style="{ paddingLeft: `${8 + Math.max(0, heading.level - 1) * 12}px` }"
      :aria-current="heading.id === activeHeadingId ? 'location' : undefined"
      @click="emit('select', heading)"
    >
      {{ heading.text }}
    </button>
  </nav>
  <p v-else class="px-3 py-8 text-center text-sm text-muted-foreground">此版本没有可识别的标题</p>
</template>
