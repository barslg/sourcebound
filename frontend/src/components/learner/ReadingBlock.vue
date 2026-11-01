<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ content: Record<string, unknown> }>()
const body = computed(() => String(props.content.body ?? ''))
const segments = computed(() => (Array.isArray(props.content.segments) ? (props.content.segments as Array<Record<string, unknown>>) : []))
const citations = computed(() => (Array.isArray(props.content.citations) ? (props.content.citations as Array<Record<string, unknown>>) : []))
</script>

<template>
  <div class="stack reading" data-block="reading">
    <p><template v-for="(s, i) in segments" :key="i"><span v-if="s.handle" class="chip mono" data-handle-chip>{{ String(s.handle) }}</span><span v-else>{{ String(s.text ?? '') }}</span></template><span v-if="segments.length === 0">{{ body }}</span></p>
    <ul v-if="citations.length > 0" class="plain-list">
      <li v-for="(c, i) in citations" :key="i" class="muted"><q>{{ String(c.quote ?? '') }}</q></li>
    </ul>
  </div>
</template>
