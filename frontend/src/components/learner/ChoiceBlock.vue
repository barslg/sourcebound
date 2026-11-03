<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Draft } from '@/composables/learnAnswer'

const props = defineProps<{ content: Record<string, unknown>; uid?: string; modelValue?: Draft; locked?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: Draft] }>()
const { t } = useI18n()
const options = computed(() => (Array.isArray(props.content.options) ? (props.content.options as unknown[]).map(String) : []))
const group = computed(() => `choice-${props.uid ?? 'x'}`)
const multiple = computed(() => props.content.multiple === true)
const ticked = (i: number): boolean => (Array.isArray(props.modelValue?.picked) ? props.modelValue.picked.includes(i) : props.modelValue?.picked === i)
function toggle(i: number): void {
  if (!props.modelValue) return
  if (!multiple.value) { emit('update:modelValue', { ...props.modelValue, picked: i }); return }
  const now = Array.isArray(props.modelValue.picked) ? props.modelValue.picked : []
  emit('update:modelValue', { ...props.modelValue, picked: now.includes(i) ? now.filter((x) => x !== i) : [...now, i].sort((a, b) => a - b) })
}
</script>

<template>
  <div v-if="modelValue === undefined" class="stack" data-block="choice">
    <p>{{ String(content.stem ?? '') }}</p>
    <ol type="A" data-testid="learner-options"><li v-for="(o, i) in options" :key="i">{{ o }}</li></ol>
  </div>
  <fieldset v-else class="stack answer-set" data-block="choice">
    <legend>{{ String(content.stem ?? '') }}</legend>
    <p v-if="multiple" class="hint">{{ t('learn.tickAll') }}</p>
    <label v-for="(o, i) in options" :key="i" class="answer-option">
      <input :type="multiple ? 'checkbox' : 'radio'" :name="group" :value="i" :checked="ticked(i)" :disabled="locked" data-testid="choice-option" @change="toggle(i)" />
      <span>{{ o }}</span>
    </label>
  </fieldset>
</template>
