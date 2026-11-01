<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { request } from '@/api/client'
import { courseEstimate, courseGenerate, coursePlan, courseRuns, runCancel, runItem, runResume } from '@/api/endpoints'
import type { Estimate, RunListItem } from '@/api/types'
import CourseHeader from '@/components/CourseHeader.vue'
import ActionButton from '@/components/ActionButton.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import Queued from '@/components/Queued.vue'
import ResourceState from '@/components/ResourceState.vue'
import Section from '@/components/Section.vue'
import StatusChip from '@/components/StatusChip.vue'
import { usd } from '@/composables/labels'
import { firstReason, lacking } from '@/composables/reasons'
import { notify, notifyError } from '@/composables/notify'
import type { PlanTree } from '@/composables/planTree'
import { stepName, stepWord, stoppedLine, type RunDoc } from '@/composables/runView'
import { useQueued } from '@/composables/useQueued'
import { useResource } from '@/composables/useResource'
import { useOperationsStore } from '@/stores/operations'

const { t, te } = useI18n()
const route = useRoute()
const operations = useOperationsStore()
const courseId = computed(() => String(route.params.id))
const header = ref<InstanceType<typeof CourseHeader> | null>(null)

const plan = useResource<PlanTree>(`plan:${courseId.value}`, (signal) => request<PlanTree>(coursePlan(courseId.value), { signal }))
const estimate = useResource<Estimate>(`estimate:${courseId.value}`, (signal) => request<Estimate>(courseEstimate(courseId.value), { signal }))
const runs = useResource<RunListItem[]>(`runs:${courseId.value}`, (signal) => request<RunListItem[]>(courseRuns(courseId.value), { signal }))
const hasPlan = computed(() => (plan.data.value?.modules ?? []).length > 0)
const alreadyGenerated = computed(() => (plan.data.value?.modules ?? []).some((m) => m.topics.some((x) => x.blocks.some((b) => b.status !== 'draft'))))
const latest = computed(() => (runs.data.value ?? []).find((r) => r.kind === 'generation' || r.kind === 'generate') ?? null)

const runDoc = ref<RunDoc | null>(null)
let timer: ReturnType<typeof setInterval> | null = null
async function loadRun(): Promise<void> {
  if (!latest.value) { runDoc.value = null; return }
  try { runDoc.value = await request<RunDoc>(runItem(latest.value.id)) } catch { /* keep the last known run */ }
}
const runStatus = computed(() => runDoc.value?.status ?? latest.value?.status ?? null)
const active = computed(() => runStatus.value === 'running' || runStatus.value === 'queued')
const stoppedWhy = computed(() => (runDoc.value ? stoppedLine(runDoc.value) : null))

const generating = ref(false)
const confirmRegenerate = ref(false)
const generateOp = ref<string | null>(null)
const genQueue = useQueued(() => `generate:${courseId.value}`, async () => { await Promise.all([runs.reload(true), plan.reload(true), header.value?.reload()]); await loadRun() })
const runId = computed(() => {
  const result = generateOp.value ? operations.items[generateOp.value]?.result : null
  const id = result && typeof result === 'object' ? (result as { run_id?: unknown }).run_id : null
  return typeof id === 'string' ? id : null
})

async function generate(): Promise<void> {
  generating.value = true
  try {
    const made = await request<{ operation_id: string }>(courseGenerate(courseId.value), { method: 'POST', body: { idempotency_key: crypto.randomUUID() } })
    generateOp.value = made.operation_id
    genQueue.track(made.operation_id)
  } catch (cause) {
    notifyError(cause)
  } finally {
    generating.value = false
  }
}

const working = ref<'resume' | 'cancel' | null>(null)
const confirmCancel = ref(false)
async function act(kind: 'resume' | 'cancel'): Promise<void> {
  const id = latest.value?.id
  if (!id) return
  working.value = kind
  try {
    await request(kind === 'resume' ? runResume(id) : runCancel(id), { method: 'POST' })
    notify('success', kind === 'resume' ? t('run.resumed') : t('run.cancelled'))
    confirmCancel.value = false
    await Promise.all([runs.reload(true), header.value?.reload()])
    await loadRun()
  } catch (cause) {
    notifyError(cause)
  } finally {
    working.value = null
  }
}

function roleName(key: string): string {
  const role = stepName(key, null).role
  return te(`run.role.${role}`) ? t(`run.role.${role}`) : role
}

onMounted(() => {
  void runs.reload().then(loadRun)
  timer = setInterval(() => { if (active.value || genQueue.queued.value) void Promise.all([runs.reload(true), plan.reload(true)]).then(loadRun) }, 3000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <CourseHeader ref="header" :course-id="courseId">
    <template #default="{ course, validation, heldCount }">
      <ResourceState :loading="plan.loading.value" :error="plan.error.value" :empty="false" @retry="plan.reload()">
        <section v-if="!hasPlan" class="state" data-view="empty" data-testid="generate-no-plan">
          <h2>{{ t('generate.noPlan') }}</h2>
          <p>{{ t('generate.noPlanHelp') }}</p>
          <router-link :to="`/courses/${courseId}/plan#build-plan`">{{ t('generate.goPlan') }}</router-link>
        </section>

        <template v-else>
          <Section :title="t('plan.estimate')" :help="t('plan.forecastNote')" />
          <ResourceState :loading="estimate.loading.value" :error="estimate.error.value" :empty="false" @retry="estimate.reload()">
            <p data-testid="forecast">{{ t('plan.forecastLine', { blocks: t('count.block', estimate.data.value?.blocks ?? 0), cost: usd(estimate.data.value?.cost_usd ?? '0') }) }} — {{ t('plan.forecastNote') }}</p>
          </ResourceState>

          <div id="generate" class="stack generate-box">
            <div class="row">
              <ActionButton action="generate" :label="alreadyGenerated ? t('generate.generateAgain') : t('plan.generate')" :loading="generating" :reason="firstReason(lacking('run.start'), active && t('plan.alreadyQueued'), validation && (validation.blocking_questions ?? []).length > 0 && t('plan.questionsOpen'), validation && (validation.tree_errors ?? []).length > 0 && t('plan.treeInvalid'), genQueue.queued.value && t('plan.alreadyQueued'))" data-testid="generate" @click="alreadyGenerated ? (confirmRegenerate = true) : generate()" />
              <router-link v-if="validation && (validation.blocking_questions ?? []).length > 0" :to="`/courses/${courseId}/plan#questions`" data-testid="go-questions">{{ t('generate.goQuestions') }}</router-link>
            </div>
            <Queued v-if="genQueue.queued.value" />
            <router-link v-if="runId && runId !== latest?.id" :to="`/runs/${runId}`" data-testid="open-run">{{ t('plan.openRun') }}</router-link>
          </div>

          <Section :title="t('generate.runTitle')" :help="t('generate.runHelp')" />
          <p v-if="!latest" class="muted" data-testid="generate-no-run">{{ t('generate.noRun') }}</p>
          <div v-else class="stack" data-testid="generate-run">
            <div class="row">
              <StatusChip :tone="runStatus === 'done' ? 'ok' : runStatus === 'stopped' ? 'danger' : 'info'" :label="t(`run.status.${runStatus ?? 'running'}`)" data-testid="run-status" />
              <span v-if="runStatus === 'done'">{{ t('run.lineDone') }}</span>
              <span v-else-if="stoppedWhy" data-testid="run-stopped">{{ t('run.lineStopped', { why: stoppedWhy }) }}</span>
              <span v-else-if="active">{{ t('run.lineWaiting') }}</span>
              <router-link :to="`/runs/${latest.id}`" data-testid="open-run">{{ t('plan.openRun') }}</router-link>
            </div>
            <p data-testid="run-cost">{{ t('run.cost', { spent: usd(runDoc?.cost_so_far ?? latest.cost_so_far) ?? t('run.spentUnknown'), forecast: usd(runDoc?.estimate?.cost_usd ?? estimate.data.value?.cost_usd) ?? t('run.noForecast') }) }}</p>
            <ul v-if="(runDoc?.steps ?? []).length > 0" class="plain-list">
              <li v-for="(s, i) in runDoc?.steps ?? []" :key="s.step_key" class="bell-item row" data-testid="run-step">
                <span>{{ t('run.stepNamed', { n: i + 1, role: roleName(s.step_key), subject: stepName(s.step_key, plan.data.value).subject }) }}</span>
                <StatusChip :tone="stepWord(s) === 'held' || stepWord(s) === 'stopped' ? 'warn' : stepWord(s) === 'ready' ? 'ok' : 'info'" :label="t(`run.word.${stepWord(s)}`, stepWord(s))" />
                <span v-if="s.error" class="mono danger-text">{{ s.error }}</span>
              </li>
            </ul>
            <div v-if="runStatus === 'stopped'" class="row">
              <ActionButton action="resume" :label="t('run.resume')" :loading="working === 'resume'" :reason="lacking('run.retry')" data-testid="run-resume" @click="act('resume')" />
              <span class="hint">{{ t('run.resumeHint') }}</span>
            </div>
            <div v-if="active" class="row">
              <ActionButton action="stop-run" :label="t('run.cancel')" :loading="working === 'cancel'" :reason="lacking('run.cancel')" data-testid="run-cancel" @click="confirmCancel = true" />
            </div>
            <div v-if="runStatus === 'done'" class="done-card stack" data-testid="generate-done">
              <strong>{{ t('generate.doneTitle') }}</strong>
              <div class="row">
                <router-link class="link-button" :to="`/courses/${courseId}/fix`" data-testid="go-fix">{{ t('generate.goFixHeld', { held: heldCount }) }}</router-link>
                <router-link :to="`/courses/${courseId}/publish`" data-testid="go-publish">{{ t('generate.goPublish') }}</router-link>
              </div>
              <span v-if="course" class="sr-only">{{ course.status }}</span>
            </div>
          </div>
        </template>
      </ResourceState>

      <ConfirmDialog v-model:visible="confirmRegenerate" :title="t('plan.regenerateTitle')" :body="t('plan.regenerateBody', { cost: usd(estimate.data.value?.cost_usd ?? '0') })" action="generate" :label="t('plan.generate')" :solid="false" @confirm="() => { confirmRegenerate = false; void generate() }" />
      <ConfirmDialog v-model:visible="confirmCancel" :title="t('run.cancelTitle')" :body="t('run.cancelBody')" action="stop-run" :label="t('run.cancel')" :busy="working === 'cancel'" @confirm="act('cancel')" />
    </template>
  </CourseHeader>
</template>
