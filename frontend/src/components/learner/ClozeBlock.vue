<script setup lang="ts">
import InputText from 'primevue/inputtext'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { BLANK, type Draft } from '@/composables/learnAnswer'

const props = defineProps<{ content: Record<string, unknown>; uid?: string; modelValue?: Draft; locked?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: Draft] }>()
const { t } = useI18n()
const parts = computed(() => String(props.content.text ?? '').split(BLANK))
function set(index: number, value: string): void {
  if (props.modelValue) emit('update:modelValue', { ...props.modelValue, blanks: props.modelValue.blanks.map((b, i) => (i === index ? value : b)) })
}
</script>

<template>
  <p v-if="modelValue === undefined" data-block="cloze">{{ String(content.text ?? '').replaceAll(BLANK, '______') }}</p>
  <p v-else class="cloze-line" data-block="cloze">
    <template v-for="(part, i) in parts" :key="i">
      <span>{{ part }}</span>
      <InputText v-if="i < parts.length - 1" class="cloze-input" autocomplete="off" :aria-label="t('learn.blankLabel', { n: i + 1 })" :model-value="modelValue.blanks[i] ?? ''" :disabled="locked" data-testid="cloze-blank" @update:model-value="(v: string | undefined) => set(i, v ?? '')" />
    </template>
  </p>
</template>
