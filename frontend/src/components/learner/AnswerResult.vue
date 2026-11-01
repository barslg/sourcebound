<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AnswerResult, Chip, KindCounts, SpotFeedback } from '@/api/types'
import StatusChip from '@/components/StatusChip.vue'
import CitationChip from './CitationChip.vue'

const props = defineProps<{ result: AnswerResult; type: string; openId?: string | null }>()
const emit = defineEmits<{ open: [chip: Chip] }>()
const { t } = useI18n()
const tone = computed(() => (props.result.correct ? 'ok' : props.result.partial ? 'warn' : 'info'))
const label = computed(() => (props.result.correct ? t('learn.result.correct') : props.result.partial ? t('learn.result.partial', { score: Math.round(props.result.score * 100) }) : t('learn.result.notYet')))
const spot = computed(() => (props.type === 'spot_the_error' ? (props.result.feedback as SpotFeedback) : null))
const habit = computed(() => (spot.value ? spot.value.habit_line : (props.result.feedback as { habit: string }).habit))
const ZERO: KindCounts = { hits: 0, false_alarms: 0, misses: 0 }
const rows = computed(() => {
  const all = (props.result.counters ?? {}) as Record<string, unknown>
  const course = (all.course ?? {}) as Record<string, KindCounts>
  const kinds = [...new Set([...Object.keys(all).filter((k) => k !== 'course'), ...Object.keys(course)])]
  return kinds.map((kind) => ({ kind, mine: (all[kind] as KindCounts | undefined) ?? ZERO, course: course[kind] ?? ZERO }))
})
const kindName = (k: string): string => t(`learn.kind.${k}`)
</script>

<template>
  <div class="stack answer-result" :class="result.correct ? 'answer-ok' : 'answer-no'" role="status" data-testid="answer-result" :data-correct="result.correct">
    <p class="row">
      <StatusChip :tone="tone" :label="label" data-testid="result-chip" />
      <span class="muted">{{ t('learn.result.attempt', { n: result.attempt_no }) }}</span>
    </p>
    <dl v-if="spot" class="spot-facts">
      <dt>{{ t('learn.result.wrongSentence') }}</dt><dd data-testid="spot-wrong">{{ spot.wrong_sentence || t('learn.result.noneWrong') }}</dd>
      <dt>{{ t('learn.result.trueStatement') }}</dt><dd data-testid="spot-true">{{ spot.true_statement }}</dd>
      <dt v-if="spot.passage">{{ t('learn.result.passage') }}</dt>
      <dd v-if="spot.passage"><CitationChip :chip="spot.passage" :open="openId === spot.passage.passage_id" @open="emit('open', $event)" /></dd>
    </dl>
    <p v-if="habit" class="muted" data-testid="habit-line">{{ habit }}</p>
    <p v-if="result.hint_from_source" class="row" data-testid="source-hint">
      <span>{{ t('learn.result.hint') }}</span>
      <CitationChip :chip="result.hint_from_source" :open="openId === result.hint_from_source.passage_id" @open="emit('open', $event)" />
    </p>
    <p v-if="result.correct && !spot && result.source_links.length > 0" class="row" data-testid="read-passage">
      <span>{{ t('learn.readPassage') }}</span>
      <CitationChip v-for="c in result.source_links" :key="c.passage_id" :chip="c" :open="openId === c.passage_id" @open="emit('open', $event)" />
    </p>
    <table v-if="spot && rows.length > 0" class="app-table-grid" data-testid="spot-counters">
      <caption class="hint">{{ t('learn.counters.caption') }}</caption>
      <thead><tr><th scope="col">{{ t('learn.counters.kind') }}</th><th scope="col">{{ t('learn.counters.mine') }}</th><th scope="col">{{ t('learn.counters.course') }}</th></tr></thead>
      <tbody>
        <tr v-for="r in rows" :key="r.kind">
          <th scope="row">{{ kindName(r.kind) }}</th>
          <td>{{ t('learn.counters.cells', { hits: r.mine.hits, alarms: r.mine.false_alarms, misses: r.mine.misses }) }}</td>
          <td>{{ t('learn.counters.cells', { hits: r.course.hits, alarms: r.course.false_alarms, misses: r.course.misses }) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
