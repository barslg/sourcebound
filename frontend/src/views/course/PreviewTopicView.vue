<script setup lang="ts">
import Select from 'primevue/select'
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { request } from '@/api/client'
import { coursePreview, coursePreviewPassage, previewTopic } from '@/api/endpoints'
import type { Chip, LearnOutline, LearnTopic, Term } from '@/api/types'
import CompanionPanel from '@/components/learner/CompanionPanel.vue'
import LearnBlockCard from '@/components/learner/LearnBlockCard.vue'
import LessonRail from '@/components/learner/LessonRail.vue'
import FormField from '@/components/FormField.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import Section from '@/components/Section.vue'
import { emptyDraft, type Draft } from '@/composables/learnAnswer'
import { COURSE_LANGUAGES } from '@/composables/languages'
import { useReloading } from '@/composables/useReloading'
import { useUrlParam } from '@/composables/useUrlState'

const { t } = useI18n()
const route = useRoute()
const topicId = computed(() => String(route.params.id))
const language = useUrlParam('language', 'en')
const topic = useReloading<LearnTopic>(() => request<LearnTopic>(previewTopic(topicId.value), { query: { lang: language.value } }), () => [topicId.value, language.value])
const courseId = computed(() => topic.data.value?.course_id ?? '')
const outline = ref<LearnOutline | null>(null)
const drafts = reactive<Record<string, Draft>>({})
const openId = ref<string | null>(null)
const openTerm = ref<Term | null>(null)
const served = computed(() => topic.data.value?.language ?? language.value)
const courseRef = computed(() => (topic.data.value ? { id: topic.data.value.course_id, title: outline.value?.title ?? topic.data.value.course_title ?? '' } : null))
const flat = computed(() => (outline.value?.modules ?? []).flatMap((m) => m.topics))
const at = computed(() => flat.value.findIndex((x) => x.id === topicId.value))
const next = computed(() => (at.value >= 0 ? (flat.value[at.value + 1] ?? null) : null))
const previous = computed(() => (at.value > 0 ? (flat.value[at.value - 1] ?? null) : null))
const held = computed(() => (topic.data.value?.blocks ?? []).filter((b) => b.status === 'held'))
const link = (id: string): string => `/preview/topics/${id}?language=${language.value}`
const outlineLink = computed(() => `/courses/${courseId.value}/preview?language=${language.value}`)
const passagePath = (id: string): string => coursePreviewPassage(courseId.value, id)

watch(() => topic.data.value, (doc) => {
  if (!doc) return
  for (const b of doc.blocks) if (!drafts[b.id]) drafts[b.id] = emptyDraft(b.block_type, b.content)
  if (!outline.value || outline.value.id !== doc.course_id) void loadOutline()
})
watch(topicId, () => { openId.value = null; openTerm.value = null })
async function loadOutline(): Promise<void> {
  try { outline.value = await request<LearnOutline>(coursePreview(courseId.value), { query: { lang: language.value } }) } catch { outline.value = null }
}
function open(chip: Chip): void { openTerm.value = null; openId.value = openId.value === chip.passage_id ? null : chip.passage_id }
function openTermOf(term: Term): void { openId.value = null; openTerm.value = openTerm.value?.term === term.term ? null : term }
</script>

<template>
  <PageHeader :course="courseRef" />
  <ResourceState :loading="topic.loading.value" :error="topic.error.value" :empty="topic.data.value !== null && topic.data.value.blocks.length === 0" :empty-text="t('previewTopic.empty')" @retry="topic.reload()">
    <template #empty-actions><router-link :to="outlineLink" data-testid="back-to-preview">{{ t('previewTopic.backToOutline') }}</router-link></template>
    <div v-if="topic.data.value" class="learn-layout">
      <LessonRail :blocks="topic.data.value.blocks" :state="(b) => (b.status === 'held' ? 'held' : 'open')" />
      <div class="stack learn-main">
        <Section id="topic" :title="topic.data.value.title" :help="topic.data.value.kind === 'module_test' ? t('learnTopic.testHelp') : t('learnTopic.lessonHelp')" />
        <ul v-if="topic.data.value.objectives.length > 0" class="hint" data-testid="objectives"><li v-for="o in topic.data.value.objectives" :key="o">{{ o }}</li></ul>
        <FormField id="pt-lang" :label="t('preview.language')" :help="t('preview.languageHint')">
          <Select v-model="language" input-id="pt-lang" :options="COURSE_LANGUAGES.map((code) => ({ code, label: t(`language.${code}`) }))" option-label="label" option-value="code" data-testid="preview-language" />
        </FormField>
        <p v-if="held.length > 0" class="preview-notice" role="note" data-testid="preview-held-note">{{ t('previewTopic.heldNote', held.length) }}</p>
        <p v-if="topic.data.value.next_note" class="hint" data-testid="through-line">{{ t('learnTopic.nextLine', { note: topic.data.value.next_note }) }}</p>
        <template v-for="(b, i) in topic.data.value.blocks" :key="b.id">
          <p v-if="b.status === 'held'" class="preview-notice" role="note" :data-block-id="b.id" data-testid="preview-held-block">{{ t('previewTopic.heldBlock', { reason: b.hold_reason }) }}</p>
          <LearnBlockCard v-model:draft="drafts[b.id] as Draft" :block="b" :index="i" :result="null" :test-mode="true" :open-id="openId" :open-term="openTerm?.term ?? null" :terms="topic.data.value.terms ?? []" @open="open" @term="openTermOf" />
        </template>
        <nav class="row" :aria-label="t('previewTopic.nav')" data-testid="preview-nav">
          <router-link :to="outlineLink" data-testid="back-to-preview">{{ t('previewTopic.backToOutline') }}</router-link>
          <router-link v-if="previous" :to="link(previous.id)" data-testid="previous-topic">{{ t('previewTopic.previous', { title: previous.title }) }}</router-link>
          <router-link v-if="next" :to="link(next.id)" data-testid="next-topic">{{ t('learnTopic.next', { title: next.title }) }}</router-link>
        </nav>
      </div>
      <div class="learn-side stack">
        <CompanionPanel :passage-id="openId" :term="openTerm" :language="served" :passage-path="passagePath" :sources-path="`/courses/${courseId}/preview/sources`" :glossary-path="`/courses/${courseId}/preview/glossary`" @close="openId = null; openTerm = null" />
      </div>
    </div>
  </ResourceState>
</template>
