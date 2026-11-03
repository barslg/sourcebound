<script setup lang="ts">
import { computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import MultiSelect from 'primevue/multiselect'
import { request } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import type { CourseRow } from '@/api/types'
import FormField from '@/components/FormField.vue'
import { useResource } from '@/composables/useResource'

interface TagRow { tag: string; courses: Array<{ id: string; title: string }> }
type Loaded = { kind: 'course'; rows: CourseRow[] } | { kind: 'label'; rows: TagRow[] }
const props = defineProps<{ id: string; by: 'course' | 'label'; modelValue: string[] }>()
const emit = defineEmits<{ 'update:modelValue': [value: string[]]; 'update:tags': [value: string[]] }>()
const { t } = useI18n()
/** Courses come from the course list (every visible course, tagged or not); labels from the tag index. */
const loaded = useResource<Loaded>(`scope:${props.by}:${props.id}`, async (signal) =>
  props.by === 'course'
    ? { kind: 'course', rows: await request<CourseRow[]>(ENDPOINTS.courses, { signal }) }
    : { kind: 'label', rows: await request<TagRow[]>(ENDPOINTS.tags, { signal }) })

const courses = computed(() => (loaded.data.value?.kind === 'course' ? loaded.data.value.rows : []).map((c) => ({ id: c.id, title: c.title })).sort((a, b) => a.title.localeCompare(b.title)))
const tagRows = computed(() => (loaded.data.value?.kind === 'label' ? loaded.data.value.rows : []))
const options = computed(() => (props.by === 'course'
  ? courses.value.map((c) => ({ value: c.id, label: c.title, count: 0 }))
  : tagRows.value.map((r) => ({ value: r.tag, label: r.courses.map((c) => c.title).join(', ') || t('scope.unknownCourse'), count: r.courses.length }))))
const derived = computed(() => [...props.modelValue].sort())
watch(derived, (next) => emit('update:tags', next), { immediate: true })
const copy = computed(() => (props.by === 'course' ? { label: t('scope.coursesLabel'), help: t('scope.coursesHelp') } : { label: t('scope.labelsLabel'), help: t('scope.labelsHelp') }))
</script>

<template>
  <div data-testid="scope-picker">
    <FormField :id="id" :label="copy.label" :help="copy.help">
      <MultiSelect :model-value="modelValue" :input-id="id" :options="options" option-label="label" option-value="value" display="chip" :placeholder="t('scope.placeholder')" :empty-message="t('scope.empty')" :loading="loaded.loading.value" :disabled="loaded.loading.value && options.length === 0" @update:model-value="emit('update:modelValue', $event as string[])">
        <template #option="{ option }">
          <span class="row">
            <span>{{ option.label }}</span>
            <span v-if="by === 'label'" class="count">{{ t('scope.courseCount', option.count) }}</span>
          </span>
        </template>
      </MultiSelect>
      <p v-if="loaded.error.value" class="mono danger-text" role="alert">{{ t('scope.loadError', { message: loaded.error.value.message }) }}</p>
    </FormField>
  </div>
</template>
