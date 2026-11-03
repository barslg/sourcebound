<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AnswerResult as Result, Chip, LearnBlockView, Term } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import { answered, isAnswerable, type Draft } from '@/composables/learnAnswer'
import { lacking } from '@/composables/reasons'
import AnswerResult from './AnswerResult.vue'
import BlockView from './BlockView.vue'
import CitationChip from './CitationChip.vue'

const props = defineProps<{ block: LearnBlockView; draft: Draft; result: Result | null; busy?: boolean; testMode?: boolean; openId: string | null; openTerm?: string | null; terms?: Term[]; index: number }>()
const emit = defineEmits<{ 'update:draft': [value: Draft]; check: []; open: [chip: Chip]; term: [term: Term] }>()
const { t } = useI18n()
const cardy = computed(() => props.block.block_type !== 'reading' && props.block.block_type !== 'figure')
const roleCard = computed(() => (props.block.block_type === 'reading' && (props.block.role === 'definition' || props.block.role === 'example') ? `learn-card learn-card-${props.block.role}` : ''))
const dots = computed(() => Math.min(props.block.attempts.count, 6))
const answerable = computed(() => isAnswerable(props.block.block_type))
const denied = lacking('learn.answer')
const reason = computed(() => denied ?? (answered(props.block.block_type, props.draft) ? null : t('learn.answerFirst')))
const bindings = computed(() => ({ uid: props.block.id, modelValue: answerable.value ? props.draft : undefined, chips: props.block.citations, options: props.block.passage_options, openId: props.openId, ...(props.block.block_type === 'reading' ? { terms: props.terms ?? [], openTerm: props.openTerm ?? null } : {}) }))
</script>

<template>
  <article :id="`block-${block.id}`" class="stack learn-block" :class="[`learn-block-${block.block_type}`, cardy ? 'learn-card' : roleCard]" :lang="block.language" :data-block-id="block.id" :data-type="block.block_type" data-testid="learn-block">
    <p class="row learn-block-head">
      <strong>{{ t('learn.blockTitle', { n: index + 1, type: t(`learn.type.${block.block_type}`) }) }}</strong>
      <span v-if="block.attempts.count > 0" class="attempt-dots" aria-hidden="true"><span v-for="n in dots" :key="n" class="attempt-dot attempt-dot-used"></span></span>
      <span v-if="block.attempts.count > 0" data-testid="attempts">{{ t('learn.attempts', block.attempts.count) }}</span>
    </p>
    <BlockView :type="block.block_type" :content="block.content" v-bind="bindings" @update:model-value="emit('update:draft', $event as Draft)" @open="emit('open', $event as Chip)" @term="emit('term', $event as Term)" />
    <ul v-if="block.citations.length > 0 && block.block_type !== 'reading'" class="plain-list row" :aria-label="t('learn.sources')" data-testid="block-chips">
      <li v-for="c in block.citations" :key="c.passage_id"><CitationChip :chip="c" :open="openId === c.passage_id" @open="emit('open', $event)" /></li>
    </ul>
    <p v-if="block.fallback_notice" class="preview-notice" role="note" data-testid="fallback-notice">{{ block.fallback_notice }}</p>
    <div v-if="answerable && !testMode" class="row">
      <ActionButton action="check-answer" :label="t('learn.checkAnswer')" :loading="busy" :reason="reason" data-testid="check-answer" @click="emit('check')" />
      <span class="hint">{{ t('learn.attemptsNever') }}</span>
    </div>
    <AnswerResult v-if="result" :result="result" :type="block.block_type" :open-id="openId" @open="emit('open', $event)" />
  </article>
</template>
