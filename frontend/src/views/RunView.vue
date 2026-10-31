<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { ApiError, request } from '@/api/client'
import { courseHeld, courseItem, runCancel, runItem, runResume } from '@/api/endpoints'
import type { CourseDoc, HeldDoc } from '@/api/types'
import GuardedButton from '@/components/GuardedButton.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import SectionHeading from '@/components/SectionHeading.vue'
import { usd } from '@/composables/labels'
import { firstReason, lacking } from '@/composables/reasons'
import { notify } from '@/composables/notify'
import { stepWord, stoppedLine, topicWord, type RunDoc } from '@/composables/runView'
import { useResource } from '@/composables/useResource'

const { t } = useI18n()
const route = useRoute()
const runId = computed(() => String(route.params.runId))
const run = useResource<RunDoc>(`run:${runId.value}`, (signal) => request<RunDoc>(runItem(runId.value), { signal }))
const doc = computed(() => run.data.value)
const courseId = computed(() => doc.value?.course_id ?? null)
const course = ref<CourseDoc | null>(null)
const held = ref<HeldDoc | null>(null)
const working = ref<'resume' | 'cancel' | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

const active = computed(() => doc.value?.steps.findIndex((s) => s.status === 'running') ?? -1)
const line = computed(() => {
  const d = doc.value
  if (!d) return ''
  const why = stoppedLine(d)
  if (why) return t('run.lineStopped', { why })
  if (d.status === 'done') return t('run.lineDone')
  return active.value >= 0 ? t('run.lineStep', { step: active.value + 1, total: d.steps.length }) : t('run.lineWaiting')
})
const crumbs = computed(() => [{ label: t('nav.courses'), to: '/courses' }, ...(course.value ? [{ label: course.value.title, to: `/courses/${course.value.id}/plan` }] : []), { label: t('run.title') }])

async function loadCourse(): Promise<void> {
  if (!courseId.value) return
  try {
    course.value = await request<CourseDoc>(courseItem(courseId.value))
    held.value = await request<HeldDoc>(courseHeld(courseId.value))
  } catch {
    course.value = course.value ?? null
  }
}
onMounted(() => {
  void run.reload().then(loadCourse)
  timer = setInterval(() => { if (doc.value && doc.value.status !== 'done') void run.reload(true).then(loadCourse) }, 3000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

async function act(kind: 'resume' | 'cancel'): Promise<void> {
  working.value = kind
  try {
    await request(kind === 'resume' ? runResume(runId.value) : runCancel(runId.value), { method: 'POST' })
    notify('success', kind === 'resume' ? t('run.resumed') : t('run.cancelled'))
    await run.reload(true)
  } catch (cause) {
    notify('error', cause instanceof ApiError ? cause.message : String(cause))
  } finally {
    working.value = null
  }
}
</script>

<template>
  <PageHeader :title="t('run.title')" :help="t('run.help')" :crumbs="crumbs" />
  <ResourceState :loading="run.loading.value" :error="run.error.value" :empty="doc === null" :empty-text="t('run.empty')" @retry="run.reload()">
    <div class="stack" data-testid="run">
      <div class="row">
        <span class="chip" data-testid="run-status">{{ t(`run.status.${doc?.status ?? 'running'}`) }}</span>
        <span data-testid="run-line">{{ line }}</span>
        <router-link v-if="course" :to="`/courses/${course.id}/plan`" data-testid="run-course">{{ t('run.backToCourse', { name: course.title }) }}</router-link>
      </div>
      <SectionHeading :title="t('run.costTitle')" :help="t('plan.forecastNote')" />
      <p data-testid="run-cost">{{ t('run.cost', { spent: usd(doc?.cost_so_far) ?? t('run.spentUnknown'), forecast: usd(doc?.estimate?.cost_usd) ?? t('run.noForecast') }) }}</p>
      <SectionHeading :title="t('run.topics')" :help="t('run.topicsHelp')" />
      <p v-if="(doc?.topics ?? []).length === 0" class="muted">{{ t('run.noTopics') }}</p>
      <ul class="plain-list" data-testid="run-topics">
        <li v-for="topic in doc?.topics ?? []" :key="topic.topic_id" class="bell-item row" data-testid="run-topic">
          <span>{{ topic.title }}</span>
          <span class="chip" :class="{ 'chip-warn': topicWord(topic.status) === 'held' }" :data-state="topicWord(topic.status)">{{ t(`run.word.${topicWord(topic.status)}`) }}</span>
        </li>
      </ul>
      <template v-if="held && held.blocks.length > 0">
        <SectionHeading :title="t('run.held')" :help="t('run.heldHelp')" />
        <ul class="plain-list" data-testid="run-held">
          <li v-for="b in held.blocks" :key="b.id" class="bell-item"><router-link :to="`/courses/${courseId}/fix#held-${b.id}`">{{ b.topic_title }}</router-link> <span class="muted">{{ b.hold_reason }}</span></li>
        </ul>
      </template>
      <SectionHeading :title="t('run.steps')" :help="t('run.stepsHelp')" />
      <ul class="plain-list">
        <li v-for="(s, i) in doc?.steps ?? []" :key="s.step_key" class="bell-item row" data-testid="run-step">
          <span>{{ t('run.stepN', { n: i + 1 }) }}</span>
          <span class="chip" :class="{ 'chip-warn': stepWord(s) === 'held' || stepWord(s) === 'stopped' }">{{ t(`run.word.${stepWord(s)}`, stepWord(s)) }}</span>
          <span v-if="s.error" class="mono danger-text">{{ s.error }}</span>
        </li>
      </ul>
      <div class="row">
        <GuardedButton :label="t('run.resume')" :loading="working === 'resume'" :reason="firstReason(lacking('run.retry'), doc?.status !== 'stopped' && t('run.resumeOnlyStopped'))" data-testid="run-resume" @click="act('resume')" />
        <GuardedButton :label="t('run.cancel')" severity="danger" outlined :loading="working === 'cancel'" :reason="firstReason(lacking('run.cancel'), doc?.status !== 'running' && t('run.cancelOnlyRunning'))" data-testid="run-cancel" @click="act('cancel')" />
      </div>
      <p class="hint">{{ t('run.resumeHint') }}</p>
    </div>
  </ResourceState>
</template>
