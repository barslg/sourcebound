<script setup lang="ts">
import { computed } from 'vue'
import type { Chip } from '@/api/types'
import { plainText } from '@/composables/reading'
import ReadingBlock from './ReadingBlock.vue'
const props = defineProps<{ content: Record<string, unknown>; chips?: Chip[]; openId?: string | null }>()
const emit = defineEmits<{ open: [chip: Chip] }>()
const caption = computed(() => String(props.content.caption ?? ''))
const label = computed(() => plainText(caption.value).replace(/\s+/g, ' ').trim())
const numbers = computed(() => (Array.isArray(props.content.numbers) ? (props.content.numbers as unknown[]).map(Number) : []))
const top = computed(() => Math.max(1, ...numbers.value.map((n) => Math.abs(n))))
</script>

<template>
  <figure class="stack" data-block="figure">
    <svg role="img" :aria-label="label" :viewBox="`0 0 ${numbers.length * 24} 60`" width="100%" height="60">
      <rect v-for="(n, i) in numbers" :key="i" :x="i * 24 + 4" :y="60 - (Math.abs(n) / top) * 56" width="16" :height="(Math.abs(n) / top) * 56" fill="currentColor" />
    </svg>
    <ReadingBlock caption :content="{ body: caption }" :chips="chips ?? []" :open-id="openId ?? null" @open="emit('open', $event)" />
  </figure>
</template>
