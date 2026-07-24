<script setup lang="ts">
import { Button, Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui'

defineProps<{ open: boolean; title: string; description: string; confirmLabel?: string; destructive?: boolean; busy?: boolean }>()
const emit = defineEmits<{ 'update:open': [value: boolean]; confirm: [] }>()
</script>

<template>
  <Dialog :open="open" @update:open="emit('update:open', $event)">
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{{ title }}</DialogTitle>
        <DialogDescription>{{ description }}</DialogDescription>
      </DialogHeader>
      <DialogFooter>
        <Button variant="outline" :disabled="busy" @click="emit('update:open', false)">取消</Button>
        <Button :variant="destructive ? 'destructive' : 'default'" :disabled="busy" @click="emit('confirm')">
          {{ busy ? '处理中' : (confirmLabel ?? '确认') }}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>
