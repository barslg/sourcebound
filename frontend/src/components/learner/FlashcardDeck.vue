<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Term } from '@/api/types'

/** Review cards built by code from the topic's terms and their defining sentences; no model call, no stored block. */
const props = defineProps<{ terms: Term[]; uid: string }>()
const { t } = useI18n()
const cards = computed(() => props.terms.filter((x) => x.definition.trim() !== ''))
const flipped = ref<Set<string>>(new Set())
function flip(term: string): void {
  const next = new Set(flipped.value)
  if (next.has(term)) next.delete(term)
  else next.add(term)
  flipped.value = next
}
</script>

<template>
  <section v-if="cards.length > 0" class="stack" data-testid="flashcards">
    <h2>{{ t('learn.flashcards.title') }}</h2>
    <p class="hint">{{ t('learn.flashcards.help') }}</p>
    <ul class="plain-list flashcards">
      <li v-for="(c, i) in cards" :key="c.term" class="flashcard" data-testid="flashcard">
        <button type="button" class="flashcard-face" :aria-expanded="flipped.has(c.term)" :aria-controls="`${uid}-card-${i}`" data-testid="flashcard-flip" @click="flip(c.term)">
          <span class="flashcard-term">{{ c.term }}</span>
          <span class="hint">{{ flipped.has(c.term) ? t('learn.flashcards.hide') : t('learn.flashcards.show') }}</span>
        </button>
        <p :id="`${uid}-card-${i}`" class="reading-line flashcard-back" :hidden="!flipped.has(c.term)" data-testid="flashcard-definition">{{ c.definition }}</p>
      </li>
    </ul>
  </section>
</template>
