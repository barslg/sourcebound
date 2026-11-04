<script setup lang="ts">
import { computed } from 'vue'
import type { Chip, Term } from '@/api/types'
import { renderReading } from '@/composables/reading'

const props = defineProps<{ content: Record<string, unknown>; chips?: Chip[]; terms?: Term[]; openId?: string | null; openTerm?: string | null; caption?: boolean }>()
const emit = defineEmits<{ open: [chip: Chip]; term: [term: Term] }>()
// The renderer never interprets HTML from the body (markdown-it html: false); every attribute it writes is escaped.
const html = computed(() => renderReading(String(props.content.body ?? ''), { chips: props.chips ?? [], terms: props.terms ?? [], openId: props.openId ?? null, openTerm: props.openTerm ?? null }))

function onClick(event: MouseEvent): void {
  const el = (event.target as HTMLElement).closest('[data-handle], [data-term]')
  if (!el) return
  const handle = el.getAttribute('data-handle')
  if (handle) {
    const chip = (props.chips ?? []).find((c) => c.handle === handle)
    if (chip) emit('open', chip)
    return
  }
  const name = el.getAttribute('data-term')
  const term = (props.terms ?? []).find((t) => t.term === name)
  if (term) emit('term', term)
}
</script>

<template>
  <!-- eslint-disable-next-line vue/no-v-html -- rendered by our own markdown renderer with html: false -->
  <figcaption v-if="caption" class="stack reading" data-testid="caption" @click="onClick" v-html="html"></figcaption>
  <!-- eslint-disable-next-line vue/no-v-html -- rendered by our own markdown renderer with html: false -->
  <div v-else class="stack reading" data-block="reading" @click="onClick" v-html="html"></div>
</template>
