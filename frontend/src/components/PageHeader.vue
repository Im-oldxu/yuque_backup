<script setup lang="ts">
defineProps<{ title: string; description?: string }>()
</script>

<template>
  <header class="yb-page-header">
    <div class="min-w-0">
      <h1 class="yb-page-title">{{ title }}</h1>
      <p v-if="description" class="yb-page-description">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="flex shrink-0 flex-wrap items-center gap-2">
      <slot name="actions" />
    </div>
  </header>
</template>
