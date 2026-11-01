<script setup lang="ts">
import Select from 'primevue/select'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Draft } from '@/composables/learnAnswer'

interface Item { id: string; text: string }
const props = defineProps<{ content: Record<string, unknown>; uid?: string; modelValue?: Draft; locked?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: Draft] }>()
const { t } = useI18n()
const items = (key: string): Item[] => (Array.isArray(props.content[key]) ? (props.content[key] as Array<Record<string, unknown>>).map((x, i) => ({ id: String(x.id ?? `${key}${i}`), text: String(x.text ?? '') })) : [])
const left = computed(() => items('left'))
const right = computed(() => items('right'))
const pairs = computed(() => (Array.isArray(props.content.pairs) ? (props.content.pairs as Array<Record<string, unknown>>) : []))
function pick(leftId: string, rightId: string | null): void {
  if (!props.modelValue) return
  const mapping = { ...props.modelValue.mapping }
  if (rightId === null) Reflect.deleteProperty(mapping, leftId)
  else mapping[leftId] = rightId
  emit('update:modelValue', { ...props.modelValue, mapping })
}
</script>

<template>
  <table v-if="modelValue === undefined" class="app-table-grid" data-block="matching">
    <tbody>
      <tr v-for="(l, i) in left" :key="l.id"><td>{{ l.text }}</td><td>{{ right[i]?.text }}</td></tr>
      <tr v-for="(p, i) in left.length === 0 ? pairs : []" :key="`p${i}`"><td>{{ String(p.left ?? '') }}</td><td>{{ String(p.right ?? '') }}</td></tr>
    </tbody>
  </table>
  <ul v-else class="plain-list" data-block="matching">
    <li v-for="l in left" :key="l.id" class="row">
      <span :id="`${uid}-${l.id}`" class="match-left">{{ l.text }}</span>
      <Select :model-value="modelValue.mapping[l.id] ?? null" :options="right" option-label="text" option-value="id" show-clear :placeholder="t('learn.pickMatch')" :aria-labelledby="`${uid}-${l.id}`" :disabled="locked" data-testid="match-select" @update:model-value="(v: string | null) => pick(l.id, v)" />
    </li>
  </ul>
</template>
