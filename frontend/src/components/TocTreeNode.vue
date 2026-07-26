<script setup lang="ts">
import { ref } from 'vue'
import { RouterLink } from 'vue-router'
import { BookOpenText, ChevronDown, FileText } from 'lucide-vue-next'
import type { DocumentSummary, TocNode } from '@/api'
import DocumentBackupStatus from './DocumentBackupStatus.vue'

withDefaults(defineProps<{
  node: TocNode
  activeDocumentId?: string
  documentStatuses?: Record<string, DocumentSummary | undefined>
}>(), {
  activeDocumentId: undefined,
  documentStatuses: () => ({}),
})
const open = ref(true)
</script>

<template>
  <li>
    <div class="flex min-h-8 items-center rounded-md text-sm hover:bg-accent hover:text-accent-foreground" :class="activeDocumentId === node.document_id ? 'bg-accent font-medium text-accent-foreground' : ''">
      <button
        v-if="node.children.length"
        type="button"
        class="flex size-8 shrink-0 items-center justify-center rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        :aria-expanded="open"
        :aria-label="`${open ? '折叠' : '展开'} ${node.title}`"
        @click="open = !open"
      >
        <ChevronDown class="size-3.5 text-muted-foreground transition-transform" :class="open ? '' : '-rotate-90'" />
      </button>
      <span v-else class="w-2 shrink-0" />

      <RouterLink v-if="node.document_id" :to="`/documents/${node.document_id}`" class="flex min-w-0 flex-1 items-center gap-2 self-stretch pr-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <FileText class="size-4 shrink-0 text-muted-foreground" />
        <span class="truncate">{{ node.title }}</span>
        <DocumentBackupStatus
          v-if="documentStatuses[node.document_id]"
          :latest-version-id="documentStatuses[node.document_id]?.latest_version_id"
          :completeness="documentStatuses[node.document_id]?.latest_version_completeness"
        />
      </RouterLink>
      <div v-else class="flex min-w-0 flex-1 items-center gap-2 self-stretch pr-2 font-medium text-muted-foreground">
        <BookOpenText class="size-4 shrink-0" />
        <span class="truncate">{{ node.title }}</span>
      </div>
    </div>
    <ul v-if="node.children.length && open" class="ml-3 flex flex-col gap-0.5 border-l pl-2">
      <TocTreeNode v-for="child in node.children" :key="child.id" :node="child" :active-document-id="activeDocumentId" :document-statuses="documentStatuses" />
    </ul>
  </li>
</template>
