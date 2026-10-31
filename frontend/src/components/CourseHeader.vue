<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { request } from '@/api/client'
import { courseItem, coursePlanValidate } from '@/api/endpoints'
import type { CourseDoc, Validation } from '@/api/types'
import ResourceState from '@/components/ResourceState.vue'
import { needsNow } from '@/composables/needsNow'
import { useResource } from '@/composables/useResource'

const props = defineProps<{ courseId: string }>()
const { t } = useI18n()
const course = useResource<CourseDoc>(`course:${props.courseId}`, (signal) => request<CourseDoc>(courseItem(props.courseId), { signal }))
const validation = useResource<Validation>(`validate:${props.courseId}`, (signal) => request<Validation>(coursePlanValidate(props.courseId), { signal }))

const phrase = computed(() => {
  const c = course.data.value
  if (!c) return null
  return needsNow({
    status: c.status,
    profile: c.profile !== null,
    plan: c.profile !== null,
    openQuestions: validation.data.value?.blocking_questions.length ?? 0,
    published: c.status === 'published',
  })
})
const tabs = computed(() => [
  { to: `/courses/${props.courseId}/sources`, label: t('course.tabSources') },
  { to: `/courses/${props.courseId}/plan`, label: t('course.tabPlan') },
  { to: `/courses/${props.courseId}/fix`, label: t('course.tabFix') },
  { to: `/courses/${props.courseId}/publish`, label: t('course.tabPublish') },
])
defineExpose({ reload: async () => { await Promise.all([course.reload(true), validation.reload(true)]) } })
</script>

<template>
  <ResourceState :loading="course.loading.value" :error="course.error.value" :empty="false" @retry="course.reload()">
    <header class="stack course-head" data-testid="course-head">
      <h1 class="page-title">{{ course.data.value?.title }}</h1>
      <div class="row">
        <span class="chip">{{ t(`course.status.${course.data.value?.status ?? 'draft'}`) }}</span>
        <span v-if="phrase" class="muted" data-testid="needs-now">{{ t('course.needsNow', { what: t(phrase) }) }}</span>
        <router-link v-for="tab in tabs" :key="tab.to" class="tab-link" :to="tab.to">{{ tab.label }}</router-link>
      </div>
    </header>
    <slot :course="course.data.value" :validation="validation.data.value" :reload="course.reload" :reload-validation="validation.reload" />
  </ResourceState>
</template>
