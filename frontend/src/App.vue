<script setup lang="ts">
import { RouterView } from 'vue-router'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Toaster } from '@/components/ui/sonner'
</script>

<template>
  <TooltipProvider>
    <RouterView />
    <Toaster rich-colors position="top-right" />
  </TooltipProvider>
</template>
