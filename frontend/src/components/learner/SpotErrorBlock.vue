<script setup lang="ts">
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Chip } from '@/api/types'
import FormField from '@/components/FormField.vue'
import { ERROR_KINDS, type Draft } from '@/composables/learnAnswer'
import { looksLikeCode } from '@/composables/reading'
import CitationChip from './CitationChip.vue'

const props = defineProps<{ content: Record<string, unknown>; uid?: string; modelValue?: Draft; locked?: boolean; options?: Chip[]; openId?: string | null }>()
const emit = defineEmits<{ 'update:modelValue': [value: Draft]; open: [chip: Chip] }>()
const { t } = useI18n()
const sentences = computed(() => (Array.isArray(props.content.sentences) ? (props.content.sentences as unknown[]).map(String) : []))
const kinds = computed(() => ERROR_KINDS.map((value) => ({ value, label: t(`learn.kind.${value}`) })))
function patch(part: Partial<Draft>): void {
  if (props.modelValue) emit('update:modelValue', { ...props.modelValue, ...part })
}
</script>

<template>
  <ol v-if="modelValue === undefined" data-block="spot_the_error"><li v-for="(s, i) in sentences" :key="i" class="keep-lines" :class="{ mono: looksLikeCode(s) }">{{ s }}</li></ol>
  <div v-else class="stack" data-block="spot_the_error">
    <fieldset class="stack answer-set">
      <legend>{{ t('learn.spotWhich') }}</legend>
      <label v-for="(s, i) in sentences" :key="i" class="answer-option">
        <input type="radio" :name="`spot-${uid}`" :value="String(i)" :checked="modelValue.sentence === String(i)" :disabled="locked" data-testid="spot-sentence" @change="patch({ sentence: String(i) })" />
        <span class="keep-lines" :class="{ mono: looksLikeCode(s) }"><strong class="mono">{{ i + 1 }}.</strong> {{ s }}</span>
      </label>
      <label class="answer-option">
        <input type="radio" :name="`spot-${uid}`" value="none" :checked="modelValue.sentence === 'none'" :disabled="locked" data-testid="spot-none" @change="patch({ sentence: 'none' })" />
        <span>{{ t('learn.spotNone') }}</span>
      </label>
    </fieldset>
    <template v-if="modelValue.sentence !== '' && modelValue.sentence !== 'none'">
      <FormField :id="`${uid}-kind`" :label="t('learn.spotKind')" :help="t('learn.spotKindHelp')">
        <Select :model-value="modelValue.kind || null" :input-id="`${uid}-kind`" :options="kinds" option-label="label" option-value="value" show-clear :placeholder="t('learn.pickKind')" :disabled="locked" data-testid="spot-kind" @update:model-value="(v: string | null) => patch({ kind: v ?? '' })" />
      </FormField>
      <fieldset v-if="(options ?? []).length > 0" class="stack answer-set">
        <legend>{{ t('learn.spotPassage') }}</legend>
        <label v-for="o in options" :key="o.passage_id" class="answer-option">
          <input type="radio" :name="`spot-passage-${uid}`" :value="o.passage_id" :checked="modelValue.passage === o.passage_id" :disabled="locked" data-testid="spot-passage" @change="patch({ passage: o.passage_id })" />
          <span>{{ o.source_title }} <span class="muted">{{ o.address_label }}</span></span>
          <CitationChip :chip="o" :open="openId === o.passage_id" @open="emit('open', $event)" />
        </label>
      </fieldset>
      <FormField :id="`${uid}-fix`" :label="t('learn.spotFix')" :help="t('learn.spotFixHelp')">
        <InputText :id="`${uid}-fix`" autocomplete="off" :model-value="modelValue.corrected" :disabled="locked" data-testid="spot-fix" @update:model-value="(v: string | undefined) => patch({ corrected: v ?? '' })" />
      </FormField>
    </template>
  </div>
</template>
