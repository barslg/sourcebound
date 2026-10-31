<script setup lang="ts">
import Button from 'primevue/button'

defineOptions({ inheritAttrs: false })
defineProps<{ label: string; reason?: string | null; loading?: boolean; severity?: 'secondary' | 'danger' | 'success'; size?: 'small' | 'large'; outlined?: boolean; text?: boolean; type?: 'button' | 'submit' }>()
</script>

<template>
  <span class="guarded">
    <Button v-bind="$attrs" :label="label" :loading="loading" :severity="severity" :size="size" :outlined="outlined" :text="text" :type="type ?? 'button'" :aria-busy="loading" :disabled="Boolean(reason)" />
    <span v-if="reason" class="hint" data-testid="reason">{{ reason }}</span>
  </span>
</template>
