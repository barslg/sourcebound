<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { Circle, CircleCheck, TriangleAlert } from 'lucide-vue-next'
import type { LearnBlockView } from '@/api/types'

export type RailState = 'done' | 'open' | 'held'
defineProps<{ blocks: LearnBlockView[]; state: (block: LearnBlockView) => RailState }>()
const { t } = useI18n()
const ICON = { done: CircleCheck, open: Circle, held: TriangleAlert } as const
</script>

<template>
  <nav class="learn-rail" :aria-label="t('learn.rail')" data-testid="lesson-rail">
    <ol>
      <li v-for="(b, i) in blocks" :key="b.id">
        <a :href="`#block-${b.id}`" :data-done="state(b) === 'done'" :data-state="state(b)">
          <component :is="ICON[state(b)]" :size="16" :stroke-width="2" :class="{ 'tick-open': state(b) === 'open', 'tick-held': state(b) === 'held' }" aria-hidden="true" />
          <span>{{ i + 1 }}. {{ t(`learn.type.${b.block_type}`) }}<span class="sr-only"> ({{ t(`learn.railState.${state(b)}`) }})</span></span>
        </a>
      </li>
    </ol>
  </nav>
</template>
