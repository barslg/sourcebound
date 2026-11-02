<script setup lang="ts">
import { computed } from 'vue'
import type { Chip } from '@/api/types'
const props = defineProps<{ content: Record<string, unknown>; chips?: Chip[]; openId?: string | null }>()
const emit = defineEmits<{ open: [chip: Chip] }>()
const body = computed(() => String(props.content.body ?? ''))
const segments = computed(() => (Array.isArray(props.content.segments) ? (props.content.segments as Array<Record<string, unknown>>) : []))
const citations = computed(() => (Array.isArray(props.content.citations) ? (props.content.citations as Array<Record<string, unknown>>) : []))
const chipOf = (handle: unknown): Chip | null => (props.chips ?? []).find((c) => c.handle === String(handle)) ?? null
</script>

<template>
  <div class="stack reading" data-block="reading">
    <p>
      <template v-for="(s, i) in segments" :key="i">
        <button v-if="s.handle && chipOf(s.handle)" type="button" class="chip mono cite-chip" :aria-expanded="openId === chipOf(s.handle)?.passage_id" aria-controls="companion-panel" :title="chipOf(s.handle)?.address_label" data-handle-chip @click="emit('open', chipOf(s.handle) as Chip)">{{ String(s.handle) }}</button>
        <span v-else-if="s.handle" class="chip mono" data-handle-chip>{{ String(s.handle) }}</span>
        <span v-else>{{ String(s.text ?? '') }}</span>
      </template>
      <span v-if="segments.length === 0">{{ body }}</span>
    </p>
    <ul v-if="citations.length > 0" class="plain-list">
      <li v-for="(c, i) in citations" :key="i" class="muted"><q>{{ String(c.quote ?? '') }}</q></li>
    </ul>
  </div>
</template>
