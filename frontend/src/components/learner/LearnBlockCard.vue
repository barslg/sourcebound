<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AnswerResult as Result, Chip, LearnBlockView } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import { answered, isAnswerable, type Draft } from '@/composables/learnAnswer'
import { lacking } from '@/composables/reasons'
import AnswerResult from './AnswerResult.vue'
import BlockView from './BlockView.vue'
import CitationChip from './CitationChip.vue'

const props = defineProps<{ block: LearnBlockView; draft: Draft; result: Result | null; busy?: boolean; testMode?: boolean; openId: string | null; index: number }>()
const emit = defineEmits<{ 'update:draft': [value: Draft]; check: []; open: [chip: Chip] }>()
const { t } = useI18n()
const answerable = computed(() => isAnswerable(props.block.block_type))
const denied = lacking('learn.answer')
const reason = computed(() => denied ?? (answered(props.block.block_type, props.draft) ? null : t('learn.answerFirst')))
const bindings = computed(() => ({ uid: props.block.id, modelValue: answerable.value ? props.draft : undefined, chips: props.block.citations, options: props.block.passage_options, openId: props.openId }))
</script>

<template>
  <article class="bell-item stack learn-block" :lang="block.language" :data-block-id="block.id" :data-type="block.block_type" data-testid="learn-block">
    <p class="row">
      <strong>{{ t('learn.blockTitle', { n: index + 1, type: t(`learn.type.${block.block_type}`) }) }}</strong>
      <span v-if="block.attempts.count > 0" class="muted" data-testid="attempts">{{ t('learn.attempts', block.attempts.count) }}</span>
    </p>
    <BlockView :type="block.block_type" :content="block.content" v-bind="bindings" @update:model-value="emit('update:draft', $event as Draft)" @open="emit('open', $event as Chip)" />
    <ul v-if="block.citations.length > 0" class="plain-list row" :aria-label="t('learn.sources')" data-testid="block-chips">
      <li v-for="c in block.citations" :key="c.passage_id"><CitationChip :chip="c" :open="openId === c.passage_id" @open="emit('open', $event)" /></li>
    </ul>
    <p v-if="block.fallback_notice" class="preview-notice" role="note" data-testid="fallback-notice">{{ block.fallback_notice }}</p>
    <div v-if="answerable && !testMode"><ActionButton action="check-answer" :label="t('learn.checkAnswer')" :loading="busy" :reason="reason" data-testid="check-answer" @click="emit('check')" /></div>
    <AnswerResult v-if="result" :result="result" :type="block.block_type" :open-id="openId" @open="emit('open', $event)" />
  </article>
</template>
