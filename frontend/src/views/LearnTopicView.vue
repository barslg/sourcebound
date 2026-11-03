<script setup lang="ts">
import Select from 'primevue/select'
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { request } from '@/api/client'
import { learnAnswer, learnCourse, learnSubmit, learnTopic } from '@/api/endpoints'
import type { AnswerResult, Chip, LearnOutline, LearnProgress, LearnTopic, SubmitResult } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import CitationChip from '@/components/learner/CitationChip.vue'
import CompanionPanel from '@/components/learner/CompanionPanel.vue'
import LearnBlockCard from '@/components/learner/LearnBlockCard.vue'
import LessonRail from '@/components/learner/LessonRail.vue'
import TutorPanel from '@/components/learner/TutorPanel.vue'
import FormField from '@/components/FormField.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import Section from '@/components/Section.vue'
import StatusChip from '@/components/StatusChip.vue'
import { emptyDraft, pageAnswers, payloadOf, type Draft } from '@/composables/learnAnswer'
import { notifyError } from '@/composables/notify'
import { lacking } from '@/composables/reasons'
import { useReloading } from '@/composables/useReloading'
import { useUrlParam } from '@/composables/useUrlState'

const { t, te } = useI18n()
const route = useRoute()
const id = computed(() => String(route.params.id))
const language = useUrlParam('language', '')
const topic = useReloading<LearnTopic>(() => request<LearnTopic>(learnTopic(id.value), { query: { lang: language.value } }), () => [id.value, language.value])
const outline = ref<LearnOutline | null>(null)
const drafts = reactive<Record<string, Draft>>({})
const results = reactive<Record<string, AnswerResult>>({})
const busy = reactive<Record<string, boolean>>({})
const progress = ref<LearnProgress | null>(null)
const submitted = ref<SubmitResult | null>(null)
const submitting = ref(false)
const openId = ref<string | null>(null)
const served = computed(() => topic.data.value?.language ?? (language.value || 'en'))
const isTest = computed(() => topic.data.value?.kind === 'module_test')
const denied = lacking('learn.answer')

watch(() => topic.data.value, (doc) => {
  if (!doc) return
  progress.value = doc.progress
  for (const b of doc.blocks) if (!drafts[b.id]) drafts[b.id] = emptyDraft(b.block_type, b.content)
}, { immediate: true })
watch(() => topic.data.value?.course_id, async (courseId) => {
  if (!courseId) return
  try { outline.value = await request<LearnOutline>(learnCourse(courseId)) } catch { outline.value = null }
}, { immediate: true })
watch(id, () => { submitted.value = null; openId.value = null; for (const k of Object.keys(results)) Reflect.deleteProperty(results, k) })

const courseRef = computed(() => (topic.data.value ? { id: topic.data.value.course_id, title: outline.value?.title ?? topic.data.value.course_title ?? '' } : null))
const languages = computed(() => {
  const o = outline.value
  if (!o) return []
  const codes = [o.source_language, ...o.available_languages.filter((l) => l.status === 'ready' || l.status === 'partial').map((l) => l.language)]
  return [...new Set(codes)].map((code) => ({ code, label: te(`language.${code}`) ? t(`language.${code}`) : code }))
})
const flat = computed(() => (outline.value?.modules ?? []).flatMap((m) => m.topics))
const next = computed(() => { const at = flat.value.findIndex((x) => x.id === id.value); return at >= 0 ? (flat.value[at + 1] ?? null) : null })
const answeredCount = computed(() => (topic.data.value?.blocks ?? []).filter((b) => payloadOf(b.block_type, drafts[b.id] ?? emptyDraft(b.block_type, b.content)) !== null).length)
const submitReason = computed(() => denied ?? (answeredCount.value === 0 ? t('learn.answerSomething') : null))
const ticked = (blockId: string): boolean => results[blockId] !== undefined || (topic.data.value?.blocks.find((b) => b.id === blockId)?.attempts.count ?? 0) > 0
const finished = computed(() => progress.value !== null && (progress.value.status === 'complete' || progress.value.status === 'mastered'))
const ratio = computed(() => { const p = progress.value; return p && p.blocks_total > 0 ? Math.round((p.blocks_attempted / p.blocks_total) * 100) : 0 })
const lessonSources = computed(() => {
  const seen = new Map<string, Chip>()
  for (const b of topic.data.value?.blocks ?? []) for (const c of b.citations) seen.set(c.passage_id, c)
  return [...seen.values()]
})
const nextLink = computed(() => (next.value ? `/learn/topics/${next.value.id}${language.value ? `?language=${language.value}` : ''}` : null))
const tone = (status: string): 'ok' | 'info' => (status === 'mastered' || status === 'complete' ? 'ok' : 'info')

function open(chip: Chip): void { openId.value = openId.value === chip.passage_id ? null : chip.passage_id }
function setLanguage(code: string): void { language.value = code === outline.value?.source_language ? '' : code }

async function check(blockId: string, type: string): Promise<void> {
  const body = payloadOf(type, drafts[blockId] as Draft)
  if (!body) return
  busy[blockId] = true
  try {
    const reply = await request<AnswerResult>(learnAnswer(blockId), { method: 'POST', body })
    results[blockId] = reply
    if (reply.topic_mastery) progress.value = reply.topic_mastery
  } catch (cause) {
    notifyError(cause, () => { void check(blockId, type) })
  } finally {
    busy[blockId] = false
  }
}

async function submit(): Promise<void> {
  const doc = topic.data.value
  if (!doc) return
  submitting.value = true
  try {
    const reply = await request<SubmitResult>(learnSubmit(doc.id), { method: 'POST', body: { answers: pageAnswers(doc.blocks, drafts) } })
    submitted.value = reply
    for (const r of reply.results) results[r.block_id] = r
    progress.value = reply.mastery
  } catch (cause) {
    notifyError(cause, () => { void submit() })
  } finally {
    submitting.value = false
  }
}

function retake(): void {
  submitted.value = null
  for (const k of Object.keys(results)) Reflect.deleteProperty(results, k)
  for (const b of topic.data.value?.blocks ?? []) drafts[b.id] = emptyDraft(b.block_type, b.content)
}
</script>

<template>
  <PageHeader :course="courseRef" />
  <ResourceState :loading="topic.loading.value" :error="topic.error.value" :empty="topic.data.value !== null && topic.data.value.blocks.length === 0" :empty-text="t('learnTopic.empty')" @retry="topic.reload()">
    <template #empty-actions><router-link v-if="topic.data.value" :to="`/learn/courses/${topic.data.value.course_id}`" data-testid="back-to-course">{{ t('learnTopic.backToCourse') }}</router-link></template>
    <div v-if="topic.data.value" class="learn-layout">
      <LessonRail :blocks="topic.data.value.blocks" :state="(b) => (ticked(b.id) ? 'done' : 'open')" />
      <div class="stack learn-main">
        <Section id="topic" :title="topic.data.value.title" :help="isTest ? t('learnTopic.testHelp') : t('learnTopic.lessonHelp')" />
        <ul v-if="topic.data.value.objectives.length > 0" class="hint" data-testid="objectives"><li v-for="o in topic.data.value.objectives" :key="o">{{ o }}</li></ul>
        <p v-if="progress" class="row muted" data-testid="topic-progress">
          <StatusChip :tone="tone(progress.status)" :label="t(`learn.status.${progress.status}`)" data-testid="topic-status" />
          <span>{{ t('learn.progress', { done: progress.blocks_attempted, total: progress.blocks_total }) }}</span>
        </p>
        <FormField v-if="languages.length > 1" id="topic-lang" :label="t('learnCourse.language')" :help="t('learnCourse.languageHelp')">
          <Select :model-value="served" input-id="topic-lang" :options="languages" option-label="label" option-value="code" data-testid="learn-language" @update:model-value="setLanguage" />
        </FormField>
        <div v-if="submitted" class="state" role="status" data-testid="test-summary">
          <h2>{{ t('learnTopic.testScore', { score: Math.round(submitted.score * 100) }) }}</h2>
          <p class="muted">{{ submitted.unanswered > 0 ? t('learnTopic.unanswered', submitted.unanswered) : t('learnTopic.allAnswered') }}</p>
          <ActionButton action="retake" :label="t('learnTopic.retake')" data-testid="retake" @click="retake" />
        </div>
        <p v-if="topic.data.value.next_note" class="hint" data-testid="through-line">{{ t('learnTopic.nextLine', { note: topic.data.value.next_note }) }}</p>
        <LearnBlockCard v-for="(b, i) in topic.data.value.blocks" :key="b.id" v-model:draft="drafts[b.id] as Draft" :block="b" :index="i" :result="results[b.id] ?? null" :busy="busy[b.id]" :test-mode="isTest" :open-id="openId" @check="check(b.id, b.block_type)" @open="open" />
        <div v-if="isTest && !submitted"><ActionButton accent action="submit-test" :label="t('learnTopic.submitTest')" :loading="submitting" :reason="submitReason" data-testid="submit-test" @click="submit" /></div>
        <footer v-if="lessonSources.length > 0" class="sources-footer stack" data-testid="sources-footer">
          <h2 class="tutor-head">{{ t('learn.sourcesFooter') }}</h2>
          <p class="hint">{{ t('learn.checkedNote') }}</p>
          <ul class="plain-list row"><li v-for="c in lessonSources" :key="c.passage_id"><CitationChip :chip="c" :open="openId === c.passage_id" @open="open" /></li></ul>
        </footer>
        <section v-if="finished" class="done-card stack" data-testid="lesson-done">
          <h2>{{ t('learn.doneTitle') }}</h2>
          <template v-if="topic.data.value.objectives.length > 0">
            <h3>{{ t('learn.takeaways') }}</h3>
            <ul class="prose"><li v-for="o in topic.data.value.objectives" :key="o">{{ o }}</li></ul>
          </template>
        </section>
        <div v-if="next" class="next-lesson" data-testid="next-topic">
          <span class="hint">{{ t('learn.nextLesson') }}</span>
          <router-link :to="nextLink as string">{{ t('learnTopic.next', { title: next.title }) }}</router-link>
        </div>
        <p v-else-if="finished" class="muted">{{ t('learn.lastLesson') }}</p>
      </div>
      <div class="learn-side stack">
        <CompanionPanel :passage-id="openId" :language="served" @close="openId = null">
          <TutorPanel :course-id="topic.data.value.course_id" :language="served" :open-id="openId" @open="open" />
        </CompanionPanel>
      </div>
    </div>
    <div v-if="topic.data.value" class="mobile-bar" data-testid="mobile-bar">
      <div class="progress-line" role="img" :aria-label="t('learn.progress', { done: progress?.blocks_attempted ?? 0, total: progress?.blocks_total ?? 0 })"><span :style="{ width: `${ratio}%` }"></span></div>
      <router-link v-if="nextLink" class="link-button" :to="nextLink">{{ t('learn.next') }}</router-link>
    </div>
  </ResourceState>
</template>
