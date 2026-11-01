<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ content: Record<string, unknown> }>()
const body = computed(() => String(props.content.body ?? ''))
const citations = computed(() => (Array.isArray(props.content.citations) ? (props.content.citations as Array<Record<string, unknown>>) : []))
</script>

<template>
  <div class="stack" data-block="reading">
    <p>{{ body }}</p>
    <ul v-if="citations.length > 0" class="plain-list">
      <li v-for="(c, i) in citations" :key="i" class="muted"><q>{{ String(c.quote ?? '') }}</q></li>
    </ul>
  </div>
</template>
