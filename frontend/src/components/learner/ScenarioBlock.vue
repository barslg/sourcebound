<script setup lang="ts">
import Select from 'primevue/select'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Chip } from '@/api/types'
import { TIERS, type Draft } from '@/composables/learnAnswer'

const props = defineProps<{ content: Record<string, unknown>; uid?: string; modelValue?: Draft; locked?: boolean; chips?: Chip[] }>()
const emit = defineEmits<{ 'update:modelValue': [value: Draft] }>()
const { t } = useI18n()
const items = computed(() => (Array.isArray(props.content.items) ? (props.content.items as unknown[]).map((x) => (typeof x === 'string' ? x : String((x as Record<string, unknown>).text ?? ''))) : []))
const tiers = computed(() => TIERS.map((value) => ({ value, label: t(`learn.tier.${value}`) })))
const sources = computed(() => (props.chips ?? []).map((c) => ({ value: c.passage_id, label: `${c.source_title} ${c.address_label}` })))
function set(field: 'labels' | 'passages', index: number, value: string | null): void {
  if (!props.modelValue) return
  const next = { ...props.modelValue[field] }
  if (value === null) Reflect.deleteProperty(next, String(index))
  else next[String(index)] = value
  emit('update:modelValue', { ...props.modelValue, [field]: next })
}
</script>

<template>
  <div v-if="modelValue === undefined" class="stack" data-block="scenario_practice">
    <p>{{ String(content.scenario ?? '') }}</p>
    <ul><li v-for="(it, i) in items" :key="i">{{ it }}</li></ul>
  </div>
  <div v-else class="stack" data-block="scenario_practice">
    <p>{{ String(content.scenario ?? '') }}</p>
    <ul class="plain-list">
      <li v-for="(it, i) in items" :key="i" class="bell-item row">
        <strong :id="`${uid}-i${i}`" class="grow">{{ it }}</strong>
        <Select :model-value="modelValue.labels[String(i)] ?? null" :options="tiers" option-label="label" option-value="value" show-clear :placeholder="t('learn.pickTier')" :aria-labelledby="`${uid}-i${i}`" :disabled="locked" data-testid="tier-select" @update:model-value="(v: string | null) => set('labels', i, v)" />
        <Select v-if="sources.length > 0" :model-value="modelValue.passages[String(i)] ?? null" :options="sources" option-label="label" option-value="value" show-clear :placeholder="t('learn.pickSource')" :aria-labelledby="`${uid}-i${i}`" :disabled="locked" data-testid="source-select" @update:model-value="(v: string | null) => set('passages', i, v)" />
      </li>
    </ul>
  </div>
</template>
