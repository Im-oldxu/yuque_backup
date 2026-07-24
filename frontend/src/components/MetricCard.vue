<script setup lang="ts">
import type { Component } from 'vue'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui'

defineProps<{ label: string; value: string | number; detail?: string; icon: Component }>()
</script>

<template>
  <Card>
    <CardHeader class="flex flex-row items-center justify-between gap-3 pb-2">
      <CardTitle class="text-sm font-medium text-muted-foreground">{{ label }}</CardTitle>
      <component :is="icon" class="size-4 text-muted-foreground" />
    </CardHeader>
    <CardContent>
      <p class="text-2xl font-semibold leading-none">{{ value }}</p>
      <p v-if="detail" class="mt-2 text-xs text-muted-foreground">{{ detail }}</p>
    </CardContent>
  </Card>
</template>
