<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Chip } from '@/api/types'
import { plainText } from '@/composables/reading'
import ReadingBlock from './ReadingBlock.vue'

interface Node { id: string; label: string }
interface Edge { from: string; to: string; label: string }
const props = defineProps<{ content: Record<string, unknown>; uid?: string; chips?: Chip[]; openId?: string | null }>()
const emit = defineEmits<{ open: [chip: Chip] }>()
const caption = computed(() => String(props.content.caption ?? ''))
const label = computed(() => plainText(caption.value).replace(/\s+/g, ' ').trim())
const { t } = useI18n()
const nodes = computed<Node[]>(() => (Array.isArray(props.content.nodes) ? (props.content.nodes as Array<Record<string, unknown>>).map((n) => ({ id: String(n.id ?? ''), label: String(n.label ?? '') })) : []))
const edges = computed<Edge[]>(() => (Array.isArray(props.content.edges) ? (props.content.edges as Array<Record<string, unknown>>).map((e) => ({ from: String(e.from ?? ''), to: String(e.to ?? ''), label: String(e.label ?? '') })) : []))
/** Mermaid entity codes: a label is text, never syntax. */
const quote = (text: string): string => `"${text.replace(/#/g, '#35;').replace(/"/g, '#quot;').replace(/</g, '#lt;').replace(/>/g, '#gt;')}"`
const ids = computed(() => new Map(nodes.value.map((n, i) => [n.id, `d${i}`])))
/** The source the browser draws, built by code from the typed graph; the model never writes it. */
const source = computed(() => {
  const lines = ['flowchart TD']
  for (const n of nodes.value) lines.push(`  ${ids.value.get(n.id)}[${quote(n.label)}]`)
  for (const e of edges.value) {
    const from = ids.value.get(e.from)
    const to = ids.value.get(e.to)
    if (from && to) lines.push(e.label.trim() === '' ? `  ${from} --> ${to}` : `  ${from} -->|${quote(e.label)}| ${to}`)
  }
  return lines.join('\n')
})
const svg = ref('')
const failed = ref(false)
async function draw(): Promise<void> {
  failed.value = false
  try {
    const mermaid = (await import('mermaid')).default
    mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'neutral', fontFamily: 'inherit' })
    const out = await mermaid.render(`diagram-${(props.uid ?? 'x').replace(/[^a-zA-Z0-9]/g, '')}`, source.value)
    svg.value = out.svg
  } catch {
    failed.value = true
  }
}
onMounted(draw)
watch(source, draw)
</script>

<template>
  <figure class="stack diagram" data-block="diagram">
    <!-- eslint-disable-next-line vue/no-v-html -- svg produced by mermaid at securityLevel strict (DOMPurify) from source our code compiled -->
    <div v-if="svg !== '' && !failed" class="diagram-canvas" role="img" :aria-label="label" data-testid="diagram-svg" v-html="svg"></div>
    <ol v-else class="plain-list" data-testid="diagram-fallback">
      <li v-for="n in nodes" :key="n.id">{{ n.label }}</li>
    </ol>
    <ReadingBlock caption :content="{ body: caption }" :chips="chips ?? []" :open-id="openId ?? null" @open="emit('open', $event)" />
    <p v-if="failed" class="muted">{{ t('learn.diagramFallback') }}</p>
  </figure>
</template>
