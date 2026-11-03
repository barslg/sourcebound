<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { GlossaryEntry } from '@/api/types'

defineProps<{ terms: GlossaryEntry[]; topicPath: (id: string) => string }>()
const { t } = useI18n()
const slug = (term: string): string => encodeURIComponent(term.toLowerCase().replace(/\s+/g, '-'))
</script>

<template>
  <dl class="glossary" data-testid="glossary">
    <template v-for="e in terms" :key="`${e.topic_id}:${e.term}`">
      <dt :id="`term-${slug(e.term)}`" data-testid="glossary-term">{{ e.term }}</dt>
      <dd>
        <p class="reading-line">{{ e.definition || t('glossary.noDefinition') }}</p>
        <p class="hint"><router-link :to="topicPath(e.topic_id)" data-testid="glossary-topic">{{ t('glossary.introducedIn', { topic: e.topic_title }) }}</router-link></p>
      </dd>
    </template>
  </dl>
</template>
