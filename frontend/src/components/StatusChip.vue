<script setup lang="ts">
import { computed } from 'vue'
import { CircleCheck, Info, OctagonX, TriangleAlert } from 'lucide-vue-next'

const props = defineProps<{ tone: 'ok' | 'warn' | 'danger' | 'info' | 'neutral'; label: string }>()
const icon = computed(() => ({ ok: CircleCheck, warn: TriangleAlert, danger: OctagonX, info: Info, neutral: Info })[props.tone])
</script>

<template>
  <span class="chip status-chip" :class="`status-${tone}`"><component :is="icon" :size="14" :stroke-width="2" aria-hidden="true" />{{ label }}</span>
</template>
