<script setup lang="ts">
import Select from 'primevue/select'
import { computed } from 'vue'
import { Circle, CircleCheck, CircleDot } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { request } from '@/api/client'
import { learnCourse, learnCourses } from '@/api/endpoints'
import type { LearnCourse, LearnOutline, LearnTopicRef } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import FormField from '@/components/FormField.vue'
import SyllabusRow from '@/components/learner/SyllabusRow.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import StatusChip from '@/components/StatusChip.vue'
import { useResource } from '@/composables/useResource'
import { useReloading } from '@/composables/useReloading'
import { useUrlParam } from '@/composables/useUrlState'

const { t, te } = useI18n()
const route = useRoute()
const router = useRouter()
const id = computed(() => String(route.params.id))
const language = useUrlParam('language', '')
const outline = useReloading<LearnOutline>(() => request<LearnOutline>(learnCourse(id.value), { query: { lang: language.value } }), () => [id.value, language.value])
const catalogue = useResource<LearnCourse[]>('learn:courses', (signal) => request<LearnCourse[]>(learnCourses, { signal }))
const audience = computed(() => (catalogue.data.value ?? []).find((c) => c.id === id.value)?.audience ?? '')
const outcomes = computed(() => [...new Set((outline.data.value?.modules ?? []).flatMap((m) => m.topics.flatMap((x) => x.objectives)))].slice(0, 5))
const ratio = computed(() => { const p = outline.data.value?.progress; return p && p.blocks_total > 0 ? Math.round((p.blocks_attempted / p.blocks_total) * 100) : 0 })
const stateIcon = (status: string) => (status === 'mastered' || status === 'complete' ? CircleCheck : status === 'in_progress' ? CircleDot : Circle)
const topics = computed<LearnTopicRef[]>(() => (outline.data.value?.modules ?? []).flatMap((m) => m.topics))
const languages = computed(() => {
  const o = outline.data.value
  if (!o) return []
  const codes = [o.source_language, ...o.available_languages.filter((l) => l.status === 'ready' || l.status === 'partial').map((l) => l.language)]
  return [...new Set(codes)].map((code) => ({ code, label: te(`language.${code}`) ? t(`language.${code}`) : code }))
})
const resume = computed(() => topics.value.find((x) => x.progress.status !== 'mastered' && x.progress.status !== 'complete') ?? topics.value[0] ?? null)
const resumeLabel = computed(() => (outline.data.value?.progress.status === 'not_started' ? t('learn.startCourse') : t('learn.continueCourse')))
const tone = (status: string): 'ok' | 'info' => (status === 'mastered' || status === 'complete' ? 'ok' : 'info')
const topicLink = (topic: LearnTopicRef): string => `/learn/topics/${topic.id}${language.value ? `?language=${language.value}` : ''}`
function setLanguage(code: string): void { language.value = code === outline.data.value?.source_language ? '' : code }
function open(): void { if (resume.value) void router.push(topicLink(resume.value)) }
</script>

<template>
  <PageHeader class="hero" :course="outline.data.value" />
  <ResourceState :loading="outline.loading.value" :error="outline.error.value" :empty="outline.data.value !== null && topics.length === 0" :empty-text="t('learnCourse.empty')" :not-found-text="t('learnCourse.notPublished')" @retry="outline.reload()">
    <template #empty-actions><router-link to="/learn" data-testid="back-to-learn">{{ t('learnCourse.backToList') }}</router-link></template>
    <template #not-found-actions>
      <router-link to="/learn" data-testid="back-to-learn">{{ t('learnCourse.backToList') }}</router-link>
      <router-link :to="`/courses/${route.params.id}/preview`" data-testid="author-preview">{{ t('learnCourse.authorPreview') }}</router-link>
    </template>
    <div v-if="outline.data.value" class="stack">
      <div class="stack" data-testid="hero">
        <p v-if="audience" class="hero-for" data-testid="hero-for">{{ t('learnCourse.forWhom', { audience }) }}</p>
        <div v-if="outcomes.length > 0">
          <h2 class="tutor-head">{{ t('learnCourse.outcomes') }}</h2>
          <ul class="hero-outcomes" data-testid="hero-outcomes"><li v-for="o in outcomes" :key="o">{{ o }}</li></ul>
        </div>
      </div>
      <div class="here-card" data-testid="here">
        <div class="stack here-card-text">
          <span class="hint">{{ t('learnCourse.youAreHere') }}</span>
          <h2>{{ resume?.title ?? '' }}</h2>
          <p class="muted flush">{{ t('learnCourse.today') }}</p>
          <div class="progress-line" role="img" :aria-label="t('learn.progress', { done: outline.data.value.progress.blocks_attempted, total: outline.data.value.progress.blocks_total })"><span :style="{ width: `${ratio}%` }"></span></div>
        </div>
        <ActionButton accent action="continue" :label="resumeLabel" :reason="resume ? null : t('learn.noTopics')" data-testid="continue" @click="open" />
      </div>
      <p class="row muted" data-testid="course-progress">
        <StatusChip :tone="tone(outline.data.value.progress.status)" :label="t(`learn.status.${outline.data.value.progress.status}`)" />
        <span>{{ t('learn.progress', { done: outline.data.value.progress.blocks_attempted, total: outline.data.value.progress.blocks_total }) }}</span>
      </p>
      <FormField v-if="languages.length > 1" id="learn-lang" :label="t('learnCourse.language')" :help="t('learnCourse.languageHelp')">
        <Select :model-value="language || outline.data.value.source_language" input-id="learn-lang" :options="languages" option-label="label" option-value="code" data-testid="learn-language" @update:model-value="setLanguage" />
      </FormField>
      <section class="stack" data-testid="course-sources">
        <h2>{{ t('learnCourse.sources') }}</h2>
        <p class="muted">{{ t('learnCourse.sourcesNote') }}</p>
      </section>
      <section v-for="m in outline.data.value.modules" :key="m.id" class="syllabus-card" data-testid="module">
        <h3>{{ m.title }}</h3>
        <p v-if="m.purpose" class="hint module-purpose">{{ m.purpose }}</p>
        <ul class="plain-list syllabus">
          <SyllabusRow v-for="x in m.topics" :key="x.id" :to="topicLink(x)" :title="x.title" :module-test="x.kind === 'module_test'" :icon="stateIcon(x.progress.status)" :icon-class="x.progress.status === 'not_started' ? 'muted' : ''">
            <template #status><StatusChip :tone="tone(x.progress.status)" :label="t(`learn.status.${x.progress.status}`)" data-testid="topic-status" /></template>
            <template #end><span class="muted">{{ t('learn.progress', { done: x.progress.blocks_attempted, total: x.progress.blocks_total }) }}</span></template>
          </SyllabusRow>
        </ul>
      </section>
    </div>
  </ResourceState>
</template>
