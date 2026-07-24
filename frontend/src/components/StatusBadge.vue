<script setup lang="ts">
import { Badge } from '@/components/ui'
import { completenessText, credentialStatusText, jobStatusText, operationStatusText, statusTone } from '@/utils/status'

const props = defineProps<{ status: string; label?: string }>()

function statusLabel(): string {
  return props.label
    ?? jobStatusText[props.status as keyof typeof jobStatusText]
    ?? credentialStatusText[props.status as keyof typeof credentialStatusText]
    ?? completenessText[props.status as keyof typeof completenessText]
    ?? operationStatusText[props.status as keyof typeof operationStatusText]
    ?? props.status
}
</script>

<template>
  <Badge :variant="statusTone(status)">{{ statusLabel() }}</Badge>
</template>
