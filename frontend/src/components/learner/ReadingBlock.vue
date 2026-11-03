<script setup lang="ts">
import { computed } from 'vue'
import type { Chip } from '@/api/types'
const props = defineProps<{ content: Record<string, unknown>; chips?: Chip[]; openId?: string | null }>()
const emit = defineEmits<{ open: [chip: Chip] }>()
type Part = { text: string; handle?: string }
const segments = computed<Part[]>(() => (Array.isArray(props.content.segments) ? (props.content.segments as Part[]) : [{ text: String(props.content.body ?? '') }]))
// paragraphs: a blank line in a text segment starts a new one; a handle chip stays inline where the writer put it
const paragraphs = computed<Part[][]>(() => {
  const out: Part[][] = [[]]
  for (const s of segments.value) {
    if (s.handle) { (out[out.length - 1] as Part[]).push(s); continue }
    const pieces = String(s.text ?? '').split(/\n\s*\n/)
    pieces.forEach((text, i) => {
      if (i > 0) out.push([])
      if (text !== '') (out[out.length - 1] as Part[]).push({ text })
    })
  }
  return out.filter((p) => p.length > 0)
})
const chipOf = (handle: unknown): Chip | null => (props.chips ?? []).find((c) => c.handle === String(handle)) ?? null
</script>

<template>
  <div class="stack reading" data-block="reading">
    <p v-for="(parts, i) in paragraphs" :key="i">
      <template v-for="(s, j) in parts" :key="j">
        <button v-if="s.handle && chipOf(s.handle)" type="button" class="chip mono cite-chip" :aria-expanded="openId === chipOf(s.handle)?.passage_id" aria-controls="companion-panel" :title="chipOf(s.handle)?.address_label" data-handle-chip @click="emit('open', chipOf(s.handle) as Chip)">{{ String(s.handle) }}</button>
        <span v-else-if="s.handle" class="chip mono" data-handle-chip>{{ String(s.handle) }}</span>
        <span v-else>{{ s.text }}</span>
      </template>
    </p>
  </div>
</template>
