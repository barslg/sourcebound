<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { request } from '@/api/client'
import { coursePreviewGlossary, coursePreviewSources, learnGlossary, learnSources } from '@/api/endpoints'
import type { GlossaryPage, SourcesPage } from '@/api/types'
import GlossaryList from '@/components/learner/GlossaryList.vue'
import SourcesList from '@/components/learner/SourcesList.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import { useReloading } from '@/composables/useReloading'

/** One page for the two reference lists of a course: its numbered sources or its glossary, read by the learner or previewed by the author. */
const props = defineProps<{ kind: 'sources' | 'glossary'; scope: 'learn' | 'preview'; backPath: (courseId: string) => string; topicPath: (topicId: string) => string }>()
const { t } = useI18n()
const route = useRoute()
const courseId = computed(() => String(route.params.id))
function load(): Promise<SourcesPage | GlossaryPage> {
  const id = courseId.value
  if (props.scope === 'learn') return props.kind === 'sources' ? request<SourcesPage>(learnSources(id)) : request<GlossaryPage>(learnGlossary(id))
  return props.kind === 'sources' ? request<SourcesPage>(coursePreviewSources(id)) : request<GlossaryPage>(coursePreviewGlossary(id))
}
const page = useReloading<SourcesPage | GlossaryPage>(load, () => [courseId.value])
const courseRef = computed(() => (page.data.value ? { id: page.data.value.course_id, title: page.data.value.title } : null))
const sources = computed(() => (page.data.value && 'sources' in page.data.value ? page.data.value.sources : []))
const terms = computed(() => (page.data.value && 'terms' in page.data.value ? page.data.value.terms : []))
const empty = computed(() => page.data.value !== null && (props.kind === 'sources' ? sources.value.length === 0 : terms.value.length === 0))
</script>

<template>
  <PageHeader :course="courseRef" />
  <ResourceState :loading="page.loading.value" :error="page.error.value" :empty="empty" :empty-text="t(kind === 'sources' ? 'learnSources.empty' : 'glossary.empty')" @retry="page.reload()">
    <template #empty-actions><router-link :to="backPath(courseId)" data-testid="back-to-course">{{ t('learnTopic.backToCourse') }}</router-link></template>
    <div v-if="page.data.value" class="stack reference-page">
      <SourcesList v-if="kind === 'sources'" :sources="sources" />
      <GlossaryList v-else :terms="terms" :topic-path="topicPath" />
      <p class="hint"><router-link :to="backPath(courseId)" data-testid="back-to-course">{{ t('learnTopic.backToCourse') }}</router-link></p>
    </div>
  </ResourceState>
</template>
