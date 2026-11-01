<script setup lang="ts">
import Select from 'primevue/select'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { request } from '@/api/client'
import { learnCourse } from '@/api/endpoints'
import type { LearnOutline, LearnTopicRef } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import FormField from '@/components/FormField.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import StatusChip from '@/components/StatusChip.vue'
import { useReloading } from '@/composables/useReloading'
import { useUrlParam } from '@/composables/useUrlState'

const { t, te } = useI18n()
const route = useRoute()
const router = useRouter()
const id = computed(() => String(route.params.id))
const language = useUrlParam('language', '')
const outline = useReloading<LearnOutline>(() => request<LearnOutline>(learnCourse(id.value), { query: { lang: language.value } }), () => [id.value, language.value])
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
  <PageHeader :course="outline.data.value">
    <template #actions>
      <ActionButton action="continue" :label="resumeLabel" :reason="resume ? null : t('learn.noTopics')" data-testid="continue" @click="open" />
    </template>
  </PageHeader>
  <ResourceState :loading="outline.loading.value" :error="outline.error.value" :empty="outline.data.value !== null && topics.length === 0" :empty-text="t('learnCourse.empty')" @retry="outline.reload()">
    <template #empty-actions><router-link to="/learn" data-testid="back-to-learn">{{ t('learnCourse.backToList') }}</router-link></template>
    <div v-if="outline.data.value" class="stack">
      <p class="row muted" data-testid="course-progress">
        <StatusChip :tone="tone(outline.data.value.progress.status)" :label="t(`learn.status.${outline.data.value.progress.status}`)" />
        <span>{{ t('learn.progress', { done: outline.data.value.progress.blocks_attempted, total: outline.data.value.progress.blocks_total }) }}</span>
      </p>
      <FormField v-if="languages.length > 1" id="learn-lang" :label="t('learnCourse.language')" :help="t('learnCourse.languageHelp')">
        <Select :model-value="language || outline.data.value.source_language" input-id="learn-lang" :options="languages" option-label="label" option-value="code" data-testid="learn-language" @update:model-value="setLanguage" />
      </FormField>
      <section v-for="m in outline.data.value.modules" :key="m.id" class="stack" data-testid="module">
        <h2>{{ m.title }}</h2>
        <p v-if="m.purpose" class="hint">{{ m.purpose }}</p>
        <ul class="plain-list">
          <li v-for="x in m.topics" :key="x.id" class="bell-item row" data-testid="topic-row">
            <router-link :to="topicLink(x)" data-testid="topic-open"><strong>{{ x.title }}</strong></router-link>
            <span v-if="x.kind === 'module_test'" class="muted">{{ t('learnCourse.moduleTest') }}</span>
            <StatusChip :tone="tone(x.progress.status)" :label="t(`learn.status.${x.progress.status}`)" data-testid="topic-status" />
            <span class="muted">{{ t('learn.progress', { done: x.progress.blocks_attempted, total: x.progress.blocks_total }) }}</span>
          </li>
        </ul>
      </section>
    </div>
  </ResourceState>
</template>
