<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  Button,
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
  Spinner,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui'
import {
  BookOpen,
  ChevronDown,
  FileText,
  ListTree,
  RefreshCw,
  Search,
  X,
} from 'lucide-vue-next'
import type { Repository, SearchResults, TocTree } from '@/api'
import type { PreviewHeading } from '@/utils/preview-outline'
import TocTreeNode from './TocTreeNode.vue'

const props = defineProps<{
  activeDocumentId: string
  activeRepositoryId: string
  outline: PreviewHeading[]
  outlineError?: string
  outlineLoading?: boolean
  repositories: Repository[]
  repositoriesError?: string
  repositoriesLoading?: boolean
  repositoryTocErrors: Record<string, string | undefined>
  repositoryTocLoadingIds: string[]
  repositoryTocs: Record<string, TocTree | undefined>
  searchError?: string
  searchLoading?: boolean
  searchQuery: string
  searchResults: SearchResults | null
  searchTerm: string
}>()

const emit = defineEmits<{
  clearSearch: []
  requestToc: [repositoryId: string, force?: boolean]
  retryOutline: []
  retryRepositories: []
  search: [query: string]
  selectHeading: [heading: PreviewHeading]
  updateSearchQuery: [query: string]
}>()

const activeTab = ref('repository')
const openRepositoryIds = ref(new Set([props.activeRepositoryId]))

const repositoryById = computed(() => {
  const result = new Map<string, Pick<Repository, 'id' | 'name'>>()
  props.repositories.forEach((repository) => result.set(repository.id, repository))
  props.searchResults?.repositories.forEach((repository) => result.set(repository.id, repository))
  return result
})

const searchGroups = computed(() => {
  const results = props.searchResults
  if (!results) return []

  const orderedIds: string[] = []
  const seen = new Set<string>()
  const addRepository = (repositoryId: string) => {
    if (seen.has(repositoryId)) return
    seen.add(repositoryId)
    orderedIds.push(repositoryId)
  }

  results.repositories.forEach((repository) => addRepository(repository.id))
  results.documents.forEach((document) => addRepository(document.repository_id))

  return orderedIds.map((repositoryId) => ({
    repository: repositoryById.value.get(repositoryId),
    documents: results.documents.filter((document) => document.repository_id === repositoryId),
  })).filter((group) => group.repository)
})

function isRepositoryOpen(repositoryId: string): boolean {
  return openRepositoryIds.value.has(repositoryId)
}

function openRepository(repositoryId: string) {
  if (!openRepositoryIds.value.has(repositoryId)) {
    openRepositoryIds.value = new Set([...openRepositoryIds.value, repositoryId])
  }
  emit('requestToc', repositoryId)
}

function toggleRepository(repositoryId: string) {
  const next = new Set(openRepositoryIds.value)
  if (next.has(repositoryId)) next.delete(repositoryId)
  else {
    next.add(repositoryId)
    emit('requestToc', repositoryId)
  }
  openRepositoryIds.value = next
}

function submitSearch() {
  emit('search', props.searchQuery.trim())
}

watch(() => props.activeRepositoryId, (repositoryId) => {
  openRepositoryIds.value = new Set([...openRepositoryIds.value, repositoryId])
  emit('requestToc', repositoryId)
})
</script>

<template>
  <div class="flex h-full min-h-0 flex-col bg-muted/15">
    <div class="border-b px-4 py-3">
      <p class="truncate text-sm font-semibold">知识库</p>
      <p class="mt-1 text-xs text-muted-foreground">本地备份目录</p>
    </div>

    <Tabs v-model="activeTab" class="flex min-h-0 flex-1 flex-col">
      <div class="border-b p-2">
        <TabsList class="grid w-full grid-cols-2" aria-label="阅读导航">
          <TabsTrigger value="repository"><BookOpen />知识库目录</TabsTrigger>
          <TabsTrigger value="outline"><ListTree />文章大纲</TabsTrigger>
        </TabsList>
      </div>

      <form v-if="activeTab === 'repository'" class="border-b p-2" role="search" @submit.prevent="submitSearch">
        <InputGroup>
          <InputGroupAddon><Search /></InputGroupAddon>
          <InputGroupInput
            :model-value="searchQuery"
            maxlength="200"
            placeholder="搜索知识库文档"
            aria-label="搜索知识库名称、文档标题或路径"
            @update:model-value="emit('updateSearchQuery', String($event ?? ''))"
          />
          <InputGroupAddon align="inline-end">
            <InputGroupButton type="submit" size="icon-xs" title="搜索" aria-label="搜索" :disabled="searchLoading">
              <Spinner v-if="searchLoading" />
              <Search v-else />
            </InputGroupButton>
            <InputGroupButton v-if="searchQuery || searchTerm" type="button" size="icon-xs" title="清除搜索" aria-label="清除搜索" @click="emit('clearSearch')">
              <X />
            </InputGroupButton>
          </InputGroupAddon>
        </InputGroup>
      </form>

      <TabsContent value="repository" class="mt-0 min-h-0 flex-1 overflow-y-auto p-2">
        <template v-if="searchTerm">
          <div v-if="searchLoading" class="flex items-center justify-center gap-2 px-3 py-8 text-sm text-muted-foreground">
            <Spinner />正在搜索
          </div>
          <div v-else-if="searchError" class="px-3 py-8 text-center">
            <p class="text-sm text-destructive">{{ searchError }}</p>
            <Button variant="outline" size="sm" class="mt-3" @click="submitSearch"><RefreshCw data-icon="inline-start" />重试</Button>
          </div>
          <div v-else-if="searchGroups.length" class="flex flex-col gap-3" aria-label="知识库搜索结果">
            <section v-for="group in searchGroups" :key="group.repository!.id">
              <button
                type="button"
                class="flex min-h-8 w-full items-center gap-2 rounded-md px-2 text-left text-sm font-medium hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                :title="group.repository!.name"
                @click="openRepository(group.repository!.id); emit('clearSearch')"
              >
                <BookOpen class="size-4 shrink-0 text-muted-foreground" />
                <span class="truncate">{{ group.repository!.name }}</span>
              </button>
              <div v-if="group.documents.length" class="ml-3 flex flex-col gap-0.5 border-l pl-2">
                <RouterLink
                  v-for="documentResult in group.documents"
                  :key="documentResult.id"
                  :to="`/documents/${documentResult.id}`"
                  class="flex min-h-8 items-center gap-2 rounded-md px-2 text-sm hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  :class="documentResult.id === activeDocumentId ? 'bg-accent font-medium text-accent-foreground' : ''"
                  :title="`${documentResult.title} · ${documentResult.path}`"
                >
                  <FileText class="size-4 shrink-0 text-muted-foreground" />
                  <span class="min-w-0 flex-1 truncate">{{ documentResult.title }}</span>
                </RouterLink>
              </div>
            </section>
          </div>
          <div v-else class="px-3 py-8 text-center">
            <p class="text-sm font-medium">没有匹配结果</p>
            <p class="mt-1 text-xs text-muted-foreground">搜索仅匹配知识库名称、文档标题、slug 和路径。</p>
          </div>
        </template>

        <template v-else>
          <div v-if="repositoriesLoading" class="flex items-center justify-center gap-2 px-3 py-8 text-sm text-muted-foreground">
            <Spinner />正在读取知识库
          </div>
          <div v-else-if="repositoriesError" class="px-3 py-8 text-center">
            <p class="text-sm text-destructive">{{ repositoriesError }}</p>
            <Button variant="outline" size="sm" class="mt-3" @click="emit('retryRepositories')"><RefreshCw data-icon="inline-start" />重试</Button>
          </div>
          <ul v-else-if="repositories.length" class="flex flex-col gap-0.5" aria-label="全部知识库目录">
            <li v-for="repository in repositories" :key="repository.id">
              <button
                type="button"
                class="flex min-h-9 w-full items-center gap-2 rounded-md px-2 text-left text-sm hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                :class="repository.id === activeRepositoryId ? 'font-medium text-foreground' : 'text-muted-foreground'"
                :aria-expanded="isRepositoryOpen(repository.id)"
                :title="repository.name"
                @click="toggleRepository(repository.id)"
              >
                <ChevronDown class="size-3.5 shrink-0 transition-transform" :class="isRepositoryOpen(repository.id) ? '' : '-rotate-90'" />
                <BookOpen class="size-4 shrink-0" />
                <span class="min-w-0 flex-1 truncate">{{ repository.name }}</span>
                <span class="shrink-0 text-xs tabular-nums text-muted-foreground">{{ repository.document_count }}</span>
              </button>

              <div v-if="isRepositoryOpen(repository.id)" class="ml-3 border-l pl-2">
                <div v-if="repositoryTocLoadingIds.includes(repository.id)" class="flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground">
                  <Spinner />正在读取目录
                </div>
                <div v-else-if="repositoryTocErrors[repository.id]" class="px-2 py-3">
                  <p class="text-xs text-destructive">{{ repositoryTocErrors[repository.id] }}</p>
                  <Button variant="ghost" size="sm" class="mt-1" @click="emit('requestToc', repository.id, true)"><RefreshCw data-icon="inline-start" />重试</Button>
                </div>
                <ul v-else-if="repositoryTocs[repository.id]?.items.length" class="flex flex-col gap-0.5 py-0.5">
                  <TocTreeNode
                    v-for="node in repositoryTocs[repository.id]?.items ?? []"
                    :key="node.id"
                    :node="node"
                    :active-document-id="activeDocumentId"
                  />
                </ul>
                <p v-else class="px-2 py-3 text-xs text-muted-foreground">暂无目录内容</p>
              </div>
            </li>
          </ul>
          <p v-else class="px-3 py-8 text-center text-sm text-muted-foreground">暂无知识库目录</p>
        </template>
      </TabsContent>

      <TabsContent value="outline" class="mt-0 min-h-0 flex-1 overflow-y-auto p-2">
        <div v-if="outlineLoading" class="flex items-center justify-center gap-2 px-3 py-8 text-sm text-muted-foreground"><Spinner />正在读取文章大纲</div>
        <div v-else-if="outlineError" class="px-3 py-8 text-center"><p class="text-sm text-destructive">{{ outlineError }}</p><Button variant="outline" size="sm" class="mt-3" @click="emit('retryOutline')"><RefreshCw data-icon="inline-start" />重试</Button></div>
        <nav v-else-if="outline.length" aria-label="文章大纲" class="flex flex-col gap-0.5">
          <button
            v-for="heading in outline"
            :key="`${heading.id}-${heading.level}`"
            type="button"
            class="min-h-8 w-full truncate rounded-md py-1.5 pr-2 text-left text-sm text-muted-foreground hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            :class="heading.level === 1 ? 'font-medium text-foreground' : ''"
            :style="{ paddingLeft: `${8 + Math.max(0, heading.level - 1) * 12}px` }"
            @click="emit('selectHeading', heading)"
          >
            {{ heading.text }}
          </button>
        </nav>
        <p v-else class="px-3 py-8 text-center text-sm text-muted-foreground">此版本没有可识别的标题</p>
      </TabsContent>
    </Tabs>
  </div>
</template>
