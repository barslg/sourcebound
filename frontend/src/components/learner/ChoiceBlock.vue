<script setup lang="ts">
import { computed } from 'vue'
import type { Draft } from '@/composables/learnAnswer'

const props = defineProps<{ content: Record<string, unknown>; uid?: string; modelValue?: Draft; locked?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: Draft] }>()
const options = computed(() => (Array.isArray(props.content.options) ? (props.content.options as unknown[]).map(String) : []))
const group = computed(() => `choice-${props.uid ?? 'x'}`)
</script>

<template>
  <div v-if="modelValue === undefined" class="stack" data-block="choice">
    <p>{{ String(content.stem ?? '') }}</p>
    <ol type="A" data-testid="learner-options"><li v-for="(o, i) in options" :key="i">{{ o }}</li></ol>
  </div>
  <fieldset v-else class="stack answer-set" data-block="choice">
    <legend>{{ String(content.stem ?? '') }}</legend>
    <label v-for="(o, i) in options" :key="i" class="answer-option">
      <input type="radio" :name="group" :value="i" :checked="modelValue.picked === i" :disabled="locked" data-testid="choice-option" @change="emit('update:modelValue', { ...modelValue, picked: i })" />
      <span>{{ o }}</span>
    </label>
  </fieldset>
</template>
