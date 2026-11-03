<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { SourceEntry } from '@/api/types'

defineProps<{ sources: SourceEntry[] }>()
const { t } = useI18n()
</script>

<template>
  <ol class="plain-list sources-list" data-testid="sources-list">
    <li v-for="s in sources" :id="`source-${s.number}`" :key="s.id" class="source-row" data-testid="source-row">
      <span class="source-number mono" data-testid="source-number">{{ s.number }}</span>
      <div class="stack source-body">
        <strong>{{ s.title }}</strong>
        <a v-if="s.address" :href="s.address" class="mono source-address" rel="noopener noreferrer external" target="_blank" data-source-address>{{ s.address }}</a>
        <p class="hint">
          <span>{{ t(`learnSources.kind.${s.kind === 'url' ? 'url' : 'upload'}`) }}</span>
          <span v-if="s.licence"> · {{ t('learnSources.licence', { licence: s.licence }) }}</span>
          <span v-if="s.as_of"> · {{ t('learnSources.asOf', { date: s.as_of }) }}</span>
          <span> · {{ s.passages_cited > 0 ? t('learnSources.cited', { passages: s.passages_cited, blocks: s.blocks_cited }) : t('learnSources.uncited') }}</span>
        </p>
      </div>
    </li>
  </ol>
</template>
