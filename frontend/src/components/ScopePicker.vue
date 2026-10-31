<script setup lang="ts">
import { computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import MultiSelect from 'primevue/multiselect'
import { request } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import FormField from '@/components/FormField.vue'
import { useResource } from '@/composables/useResource'

interface TagRow { tag: string; courses: Array<{ id: string; title: string }> }
const props = defineProps<{ id: string; by: 'course' | 'label'; modelValue: string[] }>()
const emit = defineEmits<{ 'update:modelValue': [value: string[]]; 'update:tags': [value: string[]] }>()
const { t } = useI18n()
const tags = useResource<TagRow[]>(`tags:${props.id}`, (signal) => request<TagRow[]>(ENDPOINTS.tags, { signal }))

const courses = computed(() => {
  const byId = new Map<string, { id: string; title: string; tags: string[] }>()
  for (const row of tags.data.value ?? []) {
    for (const c of row.courses) {
      const known = byId.get(c.id) ?? { id: c.id, title: c.title, tags: [] }
      known.tags.push(row.tag)
      byId.set(c.id, known)
    }
  }
  return [...byId.values()].sort((a, b) => a.title.localeCompare(b.title))
})
const options = computed(() => (props.by === 'course'
  ? courses.value.map((c) => ({ value: c.id, label: c.title, tags: c.tags, count: 0 }))
  : (tags.data.value ?? []).map((r) => ({ value: r.tag, label: r.tag, tags: [] as string[], count: r.courses.length }))))
const derived = computed(() => (props.by === 'label' ? props.modelValue : [...new Set(courses.value.filter((c) => props.modelValue.includes(c.id)).flatMap((c) => c.tags))].sort()))
watch(derived, (next) => emit('update:tags', next), { immediate: true })
const copy = computed(() => (props.by === 'course' ? { label: t('scope.coursesLabel'), help: t('scope.coursesHelp') } : { label: t('scope.labelsLabel'), help: t('scope.labelsHelp') }))
</script>

<template>
  <div data-testid="scope-picker">
    <FormField :id="id" :label="copy.label" :help="copy.help">
      <MultiSelect :model-value="modelValue" :input-id="id" :options="options" option-label="label" option-value="value" display="chip" :placeholder="t('scope.placeholder')" :empty-message="t('scope.empty')" :loading="tags.loading.value" @update:model-value="emit('update:modelValue', $event as string[])">
        <template #option="{ option }">
          <span class="row">
            <span>{{ option.label }}</span>
            <span v-for="tag in option.tags" :key="tag" class="chip mono">{{ tag }}</span>
            <span v-if="by === 'label'" class="count">{{ t('scope.courseCount', { n: option.count }) }}</span>
          </span>
        </template>
      </MultiSelect>
      <p v-if="tags.error.value" class="mono danger-text" role="alert">{{ t('scope.loadError', { message: tags.error.value.message }) }}</p>
    </FormField>
  </div>
</template>
