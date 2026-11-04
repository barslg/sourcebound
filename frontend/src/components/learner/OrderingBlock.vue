<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import ActionButton from '@/components/ActionButton.vue'
import type { Draft } from '@/composables/learnAnswer'
import { looksLikeCode } from '@/composables/reading'

interface Step { id: string; text: string }
const props = defineProps<{ content: Record<string, unknown>; uid?: string; modelValue?: Draft; locked?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: Draft] }>()
const { t } = useI18n()
const served = computed<Step[]>(() => (Array.isArray(props.content.items) ? (props.content.items as Array<Record<string, unknown>>).map((x, i) => ({ id: String(x.id ?? `s${i}`), text: String(x.text ?? '') })) : []))
const byId = computed(() => new Map(served.value.map((s) => [s.id, s])))
/** The learner's order once they moved a step; the served order until then. */
const current = computed<Step[]>(() => {
  const ids = props.modelValue?.order ?? []
  return ids.length === served.value.length ? ids.map((id) => byId.value.get(id) ?? { id, text: '' }) : served.value
})
function move(index: number, by: -1 | 1): void {
  if (!props.modelValue) return
  const ids = current.value.map((s) => s.id)
  const target = index + by
  if (target < 0 || target >= ids.length) return
  ;[ids[index], ids[target]] = [ids[target] as string, ids[index] as string]
  emit('update:modelValue', { ...props.modelValue, order: ids })
}
</script>

<template>
  <div v-if="modelValue === undefined" class="stack" data-block="ordering">
    <p>{{ String(content.prompt ?? '') }}</p>
    <ol class="plain-list" data-testid="ordering-served"><li v-for="s in served" :key="s.id" class="keep-lines" :class="{ mono: looksLikeCode(s.text) }">{{ s.text }}</li></ol>
  </div>
  <fieldset v-else class="stack answer-set" data-block="ordering">
    <legend>{{ String(content.prompt ?? '') }}</legend>
    <p class="muted">{{ t('learn.orderHelp') }}</p>
    <ol class="plain-list" data-testid="ordering-list">
      <li v-for="(s, i) in current" :key="s.id" class="row ordering-step" :data-step="s.id">
        <span class="mono">{{ i + 1 }}.</span>
        <span class="ordering-text keep-lines" :class="{ mono: looksLikeCode(s.text) }">{{ s.text }}</span>
        <span class="row-end row">
          <ActionButton action="move-up" :label="t('learn.moveStepUp', { n: i + 1 })" icon-only size="small" :reason="locked ? t('learn.locked') : i === 0 ? t('learn.alreadyFirst') : null" data-testid="step-up" @click="move(i, -1)" />
          <ActionButton action="move-down" :label="t('learn.moveStepDown', { n: i + 1 })" icon-only size="small" :reason="locked ? t('learn.locked') : i === current.length - 1 ? t('learn.alreadyLast') : null" data-testid="step-down" @click="move(i, 1)" />
        </span>
      </li>
    </ol>
  </fieldset>
</template>
