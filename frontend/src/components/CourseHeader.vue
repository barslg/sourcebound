<script setup lang="ts">
import { computed, onBeforeUnmount, watchEffect } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { request } from '@/api/client'
import { courseHeld, courseItem, coursePlan, coursePlanValidate, courseRuns, courseSources } from '@/api/endpoints'
import type { CourseDoc, HeldDoc, RunListItem, SourceRow, Validation } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusChip from '@/components/StatusChip.vue'
import ResourceState from '@/components/ResourceState.vue'
import { factsOfRow, nextAction, stages } from '@/composables/needsNow'
import type { PlanTree } from '@/composables/planTree'
import { useResource } from '@/composables/useResource'
import { lacking } from '@/composables/reasons'
import { translationLabel, translationState, translationTone } from '@/composables/translationView'
import { pathFor, STAGE_TO_ROUTE } from '@/router/registry'
import { useCourseContext } from '@/stores/courseContext'

const props = defineProps<{ courseId: string }>()
const { t, te } = useI18n()
const router = useRouter()
const route = useRoute()
const page = computed(() => String(route.name).replace(/^course-/, ''))
const previewDenied = lacking('learner.view')
const id = props.courseId
const languageName = (code: string): string => (te(`language.${code}`) ? t(`language.${code}`) : code)
const course = useResource<CourseDoc>(`course:${id}`, (signal) => request<CourseDoc>(courseItem(id), { signal }))
const validation = useResource<Validation>(`validate:${id}`, (signal) => request<Validation>(coursePlanValidate(id), { signal }))
const sources = useResource<SourceRow[]>(`sources:${id}`, (signal) => request<SourceRow[]>(courseSources(id), { signal }))
const held = useResource<HeldDoc>(`held:${id}`, (signal) => request<HeldDoc>(courseHeld(id), { signal }))
const plan = useResource<PlanTree>(`plan:${id}`, (signal) => request<PlanTree>(coursePlan(id), { signal }))
const runs = useResource<RunListItem[]>(`runs:${id}`, (signal) => request<RunListItem[]>(courseRuns(id), { signal }))

const facts = computed(() => {
  const c = course.data.value
  if (!c) return null
  const pending = (sources.data.value ?? []).filter((s) => s.approval === 'pending')
  const heldList = held.data.value?.blocks ?? []
  const blocks = (plan.data.value?.modules ?? []).flatMap((m) => m.topics.flatMap((x) => x.blocks))
  const questions = validation.data.value?.blocking_questions ?? []
  const stoppedPlan = (runs.data.value ?? []).find((r) => r.kind === 'plan' && r.status === 'stopped') ?? null
  return {
    ...factsOfRow({ ...c, has_profile: c.profile !== null, has_plan: blocks.length > 0, open_questions: questions.length }),
    firstQuestionKey: questions[0]?.key ?? null,
    firstPendingId: pending[0]?.id ?? null,
    firstHeldId: heldList[0]?.id ?? null,
    planStopped: stoppedPlan?.stop_marker ?? null,
  }
})
const action = computed(() => (facts.value ? nextAction(facts.value) : null))
const strip = computed(() => {
  const f = facts.value
  if (!f) return []
  return stages(f, f.sourceCount).map((s) => {
    const to = pathFor(STAGE_TO_ROUTE[s.id] as string, { id })
    const count = s.id === 'sources' ? (f.pendingSources > 0 ? `${f.sourceCount} · ${t('course.pendingCount', f.pendingSources)}` : String(f.sourceCount)) : s.id === 'fix' ? String(f.held + f.draft) : s.id === 'plan' && f.openQuestions > 0 ? t('course.questionCount', f.openQuestions) : ''
    return { ...s, to, count }
  })
})
const context = useCourseContext()
watchEffect(() => { if (course.data.value) context.set(id, course.data.value.title, strip.value) })
onBeforeUnmount(() => context.clear())
async function reload(): Promise<void> {
  await Promise.all([course.reload(true), validation.reload(true), sources.reload(true), held.reload(true), plan.reload(true), runs.reload(true)])
}
defineExpose({ reload })
</script>

<template>
  <ResourceState :loading="course.loading.value" :error="course.error.value" :empty="false" @retry="course.reload()">
    <div class="stack course-head" data-testid="course-head">
      <PageHeader :course="course.data.value" />
      <p v-if="course.data.value" class="course-facts muted" data-testid="course-facts">
        <span>{{ t('course.factLanguage', { name: languageName(course.data.value.language) }) }}</span>
        <span v-if="course.data.value.profile">{{ t('course.factAudience', { audience: course.data.value.profile.audience }) }}</span>
        <span>{{ t('course.factCounts', { topics: t('count.topic', course.data.value.topic_count), blocks: t('count.block', course.data.value.block_count) }) }}</span>
      </p>
      <div v-if="course.data.value" class="course-translations" data-testid="translations">
        <strong>{{ t('course.translationsLabel') }}</strong>
        <span v-if="course.data.value.translations.length === 0" class="muted">{{ t('course.translationNone') }}</span>
        <StatusChip v-for="tr in course.data.value.translations" :key="tr.language" :tone="translationTone(tr)" :label="translationLabel(tr)" :data-testid="`translation-${tr.language}`" :data-state="translationState(tr)" />
        <ActionButton action="preview" size="small" :reason="previewDenied" data-testid="preview-link" @click="router.push(`/courses/${id}/preview`)" />
        <router-link :to="`/courses/${id}/runs`" data-testid="runs-link">{{ t('runs.link') }}</router-link>
        <router-link :to="`/courses/${id}/report`" data-testid="report-link">{{ t('report.link') }}</router-link>
      </div>
      <ol class="stage-strip" :aria-label="t('course.stages')">
        <li v-for="s in strip" :key="s.id" :class="{ 'is-done': s.state === 'done' }">
          <router-link class="stage" :class="[`stage-${s.state}`, { 'stage-current': s.id === page }]" :to="s.to" :data-testid="`tab-${s.id}`">
            <strong>{{ t(`course.stage.${s.id}`) }}</strong>
            <span v-if="s.count" class="count">{{ s.count }}</span>
            <span class="count">{{ s.state === 'blocked' && s.why ? t('course.blockedBy', { why: t(`course.why.${s.why}`) }) : t(`course.state.${s.state}`) }}</span>
          </router-link>
        </li>
      </ol>
      <div v-if="action" class="next-action" data-testid="needs-now">
        <span class="muted">{{ t('course.needsNowLabel') }}</span>
        <router-link v-if="action.to" :to="action.to" data-testid="needs-link"><strong>{{ t(action.key, action.params ?? {}) }}</strong></router-link>
        <strong v-else>{{ t(action.key, action.params ?? {}) }}</strong>
      </div>
    </div>
    <slot :course="course.data.value" :validation="validation.data.value" :reload="course.reload" :reload-validation="validation.reload" :held-count="held.data.value?.blocks.length ?? 0" :draft-count="course.data.value?.draft_count ?? 0" />
  </ResourceState>
</template>
