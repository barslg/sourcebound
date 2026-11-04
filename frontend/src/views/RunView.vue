<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { request } from '@/api/client'
import { courseEstimate, courseHeld, courseItem, coursePlan, coursePlanValidate, runCancel, runItem, runResume } from '@/api/endpoints'
import type { CourseDoc, Estimate, HeldDoc, Validation } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import StatusChip from '@/components/StatusChip.vue'
import PageHeader from '@/components/PageHeader.vue'
import RunCalls from '@/components/RunCalls.vue'
import ResourceState from '@/components/ResourceState.vue'
import Section from '@/components/Section.vue'
import { usd } from '@/composables/labels'
import { holdText } from '@/composables/holdReason'
import { firstReason, lacking } from '@/composables/reasons'
import { notify, notifyError } from '@/composables/notify'
import type { PlanTree } from '@/composables/planTree'
import { stepName, stepWord, stopBlockRef, stoppedLine, topicWord, type RunDoc } from '@/composables/runView'
import { useResource } from '@/composables/useResource'

const { t, te } = useI18n()
const route = useRoute()
const runId = computed(() => String(route.params.runId))
const run = useResource<RunDoc>(`run:${runId.value}`, (signal) => request<RunDoc>(runItem(runId.value), { signal }))
const doc = computed(() => run.data.value)
const courseId = computed(() => doc.value?.course_id ?? null)
const course = ref<CourseDoc | null>(null)
const held = ref<HeldDoc | null>(null)
const plan = ref<PlanTree | null>(null)
const estimate = ref<Estimate | null>(null)
const validation = ref<Validation | null>(null)
const working = ref<'resume' | 'cancel' | null>(null)
const confirmCancel = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

const active = computed(() => doc.value?.steps.findIndex((s) => s.status === 'running') ?? -1)
const line = computed(() => {
  const d = doc.value
  if (!d) return ''
  const ref = stopBlockRef(d.stop_marker, plan.value)
  const why = ref ? t('run.stoppedAtBlock', { topic: ref.topic, ordinal: ref.ordinal }) : stoppedLine(d)
  if (why) return t('run.lineStopped', { why })
  if (d.status === 'done') return t('run.lineDone')
  return active.value >= 0 ? t('run.lineStep', { step: active.value + 1, total: d.steps.length }) : t('run.lineWaiting')
})

async function loadCourse(): Promise<void> {
  if (!courseId.value) return
  try {
    course.value = await request<CourseDoc>(courseItem(courseId.value))
    held.value = await request<HeldDoc>(courseHeld(courseId.value))
    plan.value = await request<PlanTree>(coursePlan(courseId.value))
    estimate.value = await request<Estimate>(courseEstimate(courseId.value))
    validation.value = await request<Validation>(coursePlanValidate(courseId.value))
  } catch {
    course.value = course.value ?? null
  }
}
onMounted(() => {
  void run.reload().then(loadCourse)
  timer = setInterval(() => { if (doc.value && doc.value.status !== 'done') void run.reload(true).then(loadCourse) }, 3000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

function roleName(key: string): string {
  const role = stepName(key, null).role
  return te(`run.role.${role}`) ? t(`run.role.${role}`) : role
}

async function act(kind: 'resume' | 'cancel'): Promise<void> {
  working.value = kind
  try {
    await request(kind === 'resume' ? runResume(runId.value) : runCancel(runId.value), { method: 'POST' })
    notify('success', kind === 'resume' ? t('run.resumed') : t('run.cancelled'))
    confirmCancel.value = false
    await run.reload(true)
  } catch (cause) {
    notifyError(cause)
  } finally {
    working.value = null
  }
}
</script>

<template>
  <PageHeader :course="course" />
  <ResourceState :loading="run.loading.value" :error="run.error.value" :empty="doc === null" :empty-text="t('run.empty')" @retry="run.reload()">
    <div class="stack" data-testid="run">
      <div class="row">
        <StatusChip :tone="doc?.status === 'done' ? 'ok' : doc?.status === 'stopped' ? 'danger' : 'info'" :label="t(`run.status.${doc?.status ?? 'running'}`)" data-testid="run-status" />
        <span data-testid="run-line">{{ line }}</span>
        <router-link v-if="courseId && ['empty_plan', 'no_sources'].includes(doc?.stop_code ?? '')" :to="`/courses/${courseId}/sources`" data-testid="run-back-sources">{{ t('run.backToSources') }}</router-link>
        <router-link v-if="course" :to="`/courses/${course.id}/plan`" data-testid="run-course">{{ t('run.backToCourse', { name: course.title }) }}</router-link>
      </div>
      <Section :title="t('run.costTitle')" :help="t('plan.forecastNote')" />
      <p data-testid="run-cost">{{ t('run.cost', { spent: usd(doc?.cost_so_far) ?? t('run.spentUnknown'), forecast: usd(doc?.estimate?.cost_usd ?? estimate?.cost_usd) ?? t('run.noForecast') }) }}</p>
      <RunCalls :run-id="runId" :live="doc?.status !== 'done' && doc?.status !== 'stopped'" />
      <Section :title="t('run.topics')" :help="t('run.topicsHelp')" />
      <p v-if="(doc?.topics ?? []).length === 0" class="muted">{{ t('run.noTopics') }}</p>
      <ul class="plain-list" data-testid="run-topics">
        <li v-for="topic in doc?.topics ?? []" :key="topic.topic_id" class="bell-item row" data-testid="run-topic">
          <span>{{ topic.title }}</span>
          <StatusChip :tone="topicWord(topic.status) === 'held' ? 'warn' : topicWord(topic.status) === 'ready' ? 'ok' : 'info'" :label="t(`run.word.${topicWord(topic.status)}`)" :data-state="topicWord(topic.status)" />
        </li>
      </ul>
      <template v-if="held && held.blocks.length > 0">
        <Section :title="t('run.held')" :help="t('run.heldHelp')" />
        <ul class="plain-list" data-testid="run-held">
          <li v-for="b in held.blocks" :key="b.id" class="bell-item"><router-link :to="`/courses/${courseId}/fix#held-${b.id}`">{{ b.topic_title }}</router-link> <span class="muted">{{ holdText(b.hold_reason, (id) => t(`holdItem.${id}`)) }}</span></li>
        </ul>
      </template>
      <Section :title="t('run.steps')" :help="t('run.stepsHelp')" />
      <ul class="plain-list">
        <li v-for="(s, i) in doc?.steps ?? []" :key="s.step_key" class="bell-item row" data-testid="run-step">
          <span data-testid="run-step-name">{{ t('run.stepNamed', { n: i + 1, role: roleName(s.step_key), subject: stepName(s.step_key, plan).subject }) }}</span>
          <StatusChip :tone="stepWord(s) === 'held' || stepWord(s) === 'stopped' ? 'warn' : stepWord(s) === 'ready' ? 'ok' : 'info'" :label="t(`run.word.${stepWord(s)}`, stepWord(s))" />
          <span v-if="s.error" class="mono danger-text">{{ s.error }}</span>
        </li>
      </ul>
      <div class="row">
        <ActionButton action="resume" :label="t('run.resume')" :loading="working === 'resume'" :reason="firstReason(lacking('run.retry'), doc?.status !== 'stopped' && t('run.resumeOnlyStopped'), validation && (validation.blocking_questions ?? []).length > 0 && t('plan.questionsOpen'))" data-testid="run-resume" @click="act('resume')" />
        <ActionButton action="stop-run" :label="t('run.cancel')" :loading="working === 'cancel'" :reason="firstReason(lacking('run.cancel'), doc?.status !== 'running' && t('run.cancelOnlyRunning'))" data-testid="run-cancel" @click="confirmCancel = true" />
      </div>
      <p class="hint">{{ t('run.resumeHint') }}</p>
      <ConfirmDialog v-model:visible="confirmCancel" :title="t('run.cancelTitle')" :body="t('run.cancelBody')" action="stop-run" :label="t('run.cancel')" :busy="working === 'cancel'" @confirm="act('cancel')" />
    </div>
  </ResourceState>
</template>
