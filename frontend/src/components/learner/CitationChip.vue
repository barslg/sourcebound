<script setup lang="ts">
import type { Chip } from '@/api/types'

defineProps<{ chip: Chip; open: boolean }>()
defineEmits<{ open: [chip: Chip] }>()
</script>

<template>
  <button type="button" class="chip cite-chip" :aria-expanded="open" aria-controls="companion-panel" :title="`${chip.source_title} · ${chip.address_label}`" data-testid="cite-chip" @click="$emit('open', chip)">
    <span v-if="chip.handle" class="mono">{{ chip.handle }}</span>
    <span class="cite-title">{{ chip.source_title }}</span>
    <span class="muted cite-where">{{ chip.address_short ?? chip.address_label }}</span>
  </button>
</template>
