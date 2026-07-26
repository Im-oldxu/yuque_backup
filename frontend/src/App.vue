<script setup lang="ts">
import { ref } from 'vue'
import { RouterView, useRouter } from 'vue-router'
import { DatabaseBackup } from 'lucide-vue-next'
import { Spinner } from '@/components/ui'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Toaster } from '@/components/ui/sonner'

const router = useRouter()
const routerReady = ref(router.currentRoute.value.matched.length > 0)

if (!routerReady.value) {
  void router.isReady().finally(() => { routerReady.value = true })
}
</script>

<template>
  <TooltipProvider>
    <div v-if="!routerReady" class="flex min-h-svh items-center justify-center bg-background text-foreground">
      <div class="flex items-center gap-3" aria-label="正在加载应用">
        <span class="flex size-9 items-center justify-center rounded-md bg-primary text-primary-foreground"><DatabaseBackup class="size-4" /></span>
        <span class="text-sm font-medium">Yuque Backup</span>
        <Spinner class="size-4 text-muted-foreground" />
      </div>
    </div>
    <RouterView v-else />
    <Toaster rich-colors position="top-right" />
  </TooltipProvider>
</template>
