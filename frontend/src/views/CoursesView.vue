<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { request } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import ResourceState from '@/components/ResourceState.vue'
import { useResource } from '@/composables/useResource'

interface CourseRow { id: string; title: string }
const { t } = useI18n()
const courses = useResource<CourseRow[]>('courses', (signal) => request<CourseRow[]>(ENDPOINTS.courses, { signal }))
</script>

<template>
  <h1 class="page-title">{{ t('courses.title') }}</h1>
  <ResourceState
    :loading="courses.loading.value"
    :error="courses.error.value"
    :empty="(courses.data.value ?? []).length === 0"
    :empty-text="t('courses.empty')"
    @retry="courses.reload()"
  >
    <ul data-testid="course-list">
      <li v-for="c in courses.data.value" :key="c.id">{{ c.title }}</li>
    </ul>
  </ResourceState>
</template>
