<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { request } from '@/api/client'
import { learnCourses } from '@/api/endpoints'
import type { LearnCourse } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import StatusChip from '@/components/StatusChip.vue'
import { useResource } from '@/composables/useResource'

const { t } = useI18n()
const courses = useResource<LearnCourse[]>('learn:courses', (signal) => request<LearnCourse[]>(learnCourses, { signal }))
</script>

<template>
  <PageHeader />
  <ResourceState :loading="courses.loading.value" :error="courses.error.value" :empty="(courses.data.value ?? []).length === 0" :empty-text="t('learn.empty')" @retry="courses.reload()">
    <ul class="plain-list" data-testid="learn-list">
      <li v-for="c in courses.data.value" :key="c.id" class="bell-item row" data-testid="learn-item">
        <strong>{{ c.title }}</strong>
        <span v-if="c.audience" class="muted">{{ t('course.factAudience', { audience: c.audience }) }}</span>
        <StatusChip :tone="c.progress.status === 'mastered' || c.progress.status === 'complete' ? 'ok' : 'info'" :label="t(`learn.status.${c.progress.status}`)" />
        <span class="muted">{{ t('learn.progress', { done: c.progress.blocks_attempted, total: c.progress.blocks_total }) }}</span>
      </li>
    </ul>
  </ResourceState>
</template>
