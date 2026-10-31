<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { request } from '@/api/client'
import { courseHeld, courseItem, coursePlan, coursePlanValidate, courseSources } from '@/api/endpoints'
import type { CourseDoc, HeldDoc, SourceRow, Validation } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import { nextAction, stages } from '@/composables/needsNow'
import type { PlanTree } from '@/composables/planTree'
import { useResource } from '@/composables/useResource'

const props = defineProps<{ courseId: string; page: 'sources' | 'plan' | 'fix' | 'publish' }>()
const { t } = useI18n()
const id = props.courseId
const course = useResource<CourseDoc>(`course:${id}`, (signal) => request<CourseDoc>(courseItem(id), { signal }))
const validation = useResource<Validation>(`validate:${id}`, (signal) => request<Validation>(coursePlanValidate(id), { signal }))
const sources = useResource<SourceRow[]>(`sources:${id}`, (signal) => request<SourceRow[]>(courseSources(id), { signal }))
const held = useResource<HeldDoc>(`held:${id}`, (signal) => request<HeldDoc>(courseHeld(id), { signal }))
const plan = useResource<PlanTree>(`plan:${id}`, (signal) => request<PlanTree>(coursePlan(id), { signal }))

const facts = computed(() => {
  const c = course.data.value
  if (!c) return null
  const list = sources.data.value ?? []
  const pending = list.filter((s) => s.approval === 'pending')
  const heldList = held.data.value?.blocks ?? []
  const blocks = (plan.data.value?.modules ?? []).flatMap((m) => m.topics.flatMap((x) => x.blocks))
  const questions = validation.data.value?.blocking_questions ?? []
  return {
    courseId: id,
    status: c.status,
    profile: c.profile !== null,
    plan: blocks.length > 0,
    openQuestions: questions.length,
    published: c.status === 'published',
    firstQuestionKey: questions[0]?.key ?? null,
    pendingSources: pending.length,
    firstPendingId: pending[0]?.id ?? null,
    held: heldList.length,
    firstHeldId: heldList[0]?.id ?? null,
    generated: blocks.some((b) => b.status !== 'draft'),
    sourceCount: list.length,
  }
})
const action = computed(() => (facts.value ? nextAction(facts.value) : null))
const strip = computed(() => {
  const f = facts.value
  if (!f) return []
  return stages(f, f.sourceCount).map((s) => {
    const to = s.id === 'generate' ? `/courses/${id}/plan#generate` : `/courses/${id}/${s.id}`
    const count = s.id === 'sources' ? (f.pendingSources > 0 ? `${f.sourceCount} · ${t('course.pendingCount', { n: f.pendingSources })}` : String(f.sourceCount)) : s.id === 'fix' ? String(f.held) : s.id === 'plan' && f.openQuestions > 0 ? t('course.questionCount', f.openQuestions) : ''
    return { ...s, to, count }
  })
})
const crumbs = computed(() => [{ label: t('nav.courses'), to: '/courses' }, { label: course.data.value?.title ?? '' }])
async function reload(): Promise<void> {
  await Promise.all([course.reload(true), validation.reload(true), sources.reload(true), held.reload(true), plan.reload(true)])
}
defineExpose({ reload })
</script>

<template>
  <ResourceState :loading="course.loading.value" :error="course.error.value" :empty="false" @retry="course.reload()">
    <div class="stack course-head" data-testid="course-head">
      <PageHeader :title="course.data.value?.title ?? ''" :help="t(`course.help.${props.page}`)" :crumbs="crumbs" />
      <ol class="stage-strip" :aria-label="t('course.stages')">
        <li v-for="s in strip" :key="s.id">
          <router-link class="stage" :class="[`stage-${s.state}`, { 'stage-current': s.id === props.page }]" :to="s.to" :data-testid="`tab-${s.id}`">
            <strong>{{ t(`course.stage.${s.id}`) }}</strong>
            <span v-if="s.count" class="count">{{ s.count }}</span>
            <span class="count">{{ s.state === 'blocked' && s.why ? t('course.blockedBy', { why: t(`course.why.${s.why}`) }) : t(`course.state.${s.state}`) }}</span>
          </router-link>
        </li>
      </ol>
      <div v-if="action" class="next-action" data-testid="needs-now">
        <span class="muted">{{ t('course.needsNowLabel') }}</span>
        <router-link v-if="action.to" :to="action.to" data-testid="needs-link"><strong>{{ t(action.key) }}</strong></router-link>
        <strong v-else>{{ t(action.key) }}</strong>
      </div>
    </div>
    <slot :course="course.data.value" :validation="validation.data.value" :reload="course.reload" :reload-validation="validation.reload" :held-count="held.data.value?.blocks.length ?? 0" />
  </ResourceState>
</template>
