<script setup lang="ts">
const props = defineProps<{ content: Record<string, unknown> }>()
const numbers = Array.isArray(props.content.numbers) ? (props.content.numbers as unknown[]).map(Number) : []
const top = Math.max(1, ...numbers.map((n) => Math.abs(n)))
</script>

<template>
  <figure class="stack" data-block="figure">
    <svg role="img" :aria-label="String(content.caption ?? '')" :viewBox="`0 0 ${numbers.length * 24} 60`" width="100%" height="60">
      <rect v-for="(n, i) in numbers" :key="i" :x="i * 24 + 4" :y="60 - (Math.abs(n) / top) * 56" width="16" :height="(Math.abs(n) / top) * 56" fill="currentColor" />
    </svg>
    <figcaption>{{ String(content.caption ?? '') }}</figcaption>
  </figure>
</template>
