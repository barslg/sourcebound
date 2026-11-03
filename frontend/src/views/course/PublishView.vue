<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { ApiError, request } from '@/api/client'
import { coursePlan, coursePublish, courseTranslate, ENDPOINTS } from '@/api/endpoints'
import type { CourseDoc, Invite } from '@/api/types'
import CourseHeader from '@/components/CourseHeader.vue'
import ActionButton from '@/components/ActionButton.vue'
import Checkbox from 'primevue/checkbox'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import FormField from '@/components/FormField.vue'
import Queued from '@/components/Queued.vue'
import Section from '@/components/Section.vue'
import { COURSE_LANGUAGES } from '@/composables/languages'
import { firstReason, lacking } from '@/composables/reasons'
import { notify, notifyError } from '@/composables/notify'
import type { PlanTree } from '@/composables/planTree'
import { useQueued } from '@/composables/useQueued'
import { dropCached, useResource } from '@/composables/useResource'
import { useSessionStore } from '@/stores/session'

const { t } = useI18n()
const session = useSessionStore()
/** A paid run needs a Meta key: the account's own, or the stand's local one. */
const noKey = computed(() => (session.me?.model_key_ready ? null : t('account.noKey')))
const route = useRoute()
const router = useRouter()
const courseId = computed(() => String(route.params.id))
const header = ref<InstanceType<typeof CourseHeader> | null>(null)
const plan = useResource<PlanTree>(`plan:${courseId.value}`, (signal) => request<PlanTree>(coursePlan(courseId.value), { signal }))
const topics = computed(() => (plan.data.value?.modules ?? []).reduce((n, m) => n + m.topics.length, 0))
const review = ref(false)
const busy = ref(false)
const error = ref<string | null>(null)

const languages = ref<string[]>([])
const translating = ref(false)
const translation = useQueued(() => `translate:${courseId.value}`, async () => { dropCached(`course:${courseId.value}`); await header.value?.reload() })
const targets = (course: CourseDoc | null): readonly string[] => COURSE_LANGUAGES.filter((code) => code !== (course?.language ?? 'en'))

async function translate(): Promise<void> {
  translating.value = true
  try {
    const made = await request<{ operation_id: string }>(courseTranslate(courseId.value), { method: 'POST', body: { languages: languages.value } })
    translation.track(made.operation_id)
  } catch (cause) {
    notifyError(cause, () => void translate())
  } finally {
    translating.value = false
  }
}

const studentLink = ref<string | null>(null)
const linking = ref(false)
/** One invite for this course, unlimited seats, no expiry: the address a learner opens, signs in and is enrolled. */
async function makeStudentLink(): Promise<void> {
  linking.value = true
  try {
    const invite = await request<Invite>(ENDPOINTS.invites, { method: 'POST', body: { course_tags: [courseId.value] } })
    studentLink.value = new URL(router.resolve(`/invite/${encodeURIComponent(invite.token)}`).href, window.location.origin).toString()
    notify('success', t('invites.created'))
  } catch (cause) {
    notifyError(cause, () => void makeStudentLink())
  } finally {
    linking.value = false
  }
}
async function copyStudentLink(): Promise<void> {
  if (!studentLink.value) return
  await navigator.clipboard.writeText(studentLink.value)
  notify('success', t('invites.copied'))
}

async function publish(): Promise<void> {
  busy.value = true
  error.value = null
  try {
    await request(coursePublish(courseId.value), { method: 'POST' })
    notify('success', t('publish.done'))
    review.value = false
    dropCached(`course:${courseId.value}`)
    await header.value?.reload()
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : String(cause)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <CourseHeader ref="header" :course-id="courseId">
    <template #default="{ course, validation, heldCount, draftCount }">
      <section class="stack">
        <Section id="summary" :title="t('publish.summary')" :help="t('publish.summaryHelp')" />
        <ul class="plain-list" data-testid="publish-summary">
          <li>{{ t('publish.reviewStatus', { status: t(`course.status.${course?.status ?? 'draft'}`) }) }}</li>
          <li>{{ t('publish.topics', { count: t('count.topic', topics) }) }}</li>
          <li>{{ t('publish.held', { count: t('count.block', heldCount) }) }} <router-link v-if="heldCount > 0" :to="`/courses/${courseId}/fix#held-list`">{{ t('publish.fixThem') }}</router-link></li>
          <li v-if="draftCount > 0" data-testid="publish-unread">{{ t('publish.unread', { count: t('count.block', draftCount) }) }} <router-link :to="`/courses/${courseId}/fix#held-list`">{{ t('publish.fixThem') }}</router-link></li>
          <li>{{ t('publish.questions', { count: t('count.question', validation?.blocking_questions.length ?? 0) }) }}</li>
        </ul>
        <p v-if="course?.published_version_id" class="muted" data-testid="published-note">{{ t('publish.published') }}</p>
        <Section id="publish" :title="t('publish.title')" :help="t('publish.help')" />
        <div class="row">
          <ActionButton action="publish" :label="t('publish.button')" :reason="firstReason(lacking('course.publish'), heldCount > 0 && t('publish.heldFirst', { count: t('count.block', heldCount) }), draftCount > 0 && t('publish.unreadFirst', { count: t('count.block', draftCount) }))" data-testid="publish-open" @click="review = true" />
          <span id="publish-open" />
        </div>
        <Section id="translate" :title="t('translate.title')" :help="t('translate.help')" />
        <FormField id="t-langs" :label="t('translate.pick')" :help="t('translate.pickHint')" group>
          <div class="row">
            <span v-for="code in targets(course)" :key="code" class="row">
              <Checkbox v-model="languages" :input-id="`t-${code}`" :value="code" />
              <label :for="`t-${code}`">{{ t(`language.${code}`) }}</label>
            </span>
          </div>
        </FormField>
        <div class="row">
          <ActionButton action="translate" :label="t('translate.button')" :loading="translating" :reason="firstReason(noKey, lacking('course.generate'), !course?.current_version_id && t('translate.noVersion'), translation.queued.value && t('translate.alreadyQueued'), languages.length === 0 && t('translate.pickFirst'))" data-testid="translate-open" @click="translate" />
          <Queued v-if="translation.queued.value" />
        </div>
        <Section :title="t('publish.share')" :help="t('publish.shareHelp')" />
        <div class="row">
          <ActionButton action="create" :label="t('publish.makeLink')" :loading="linking" :reason="firstReason(lacking('invite.create'), !course?.published_version_id && t('publish.publishFirst'))" data-testid="make-student-link" @click="makeStudentLink" />
          <router-link :to="`/invites?course=${courseId}`" data-testid="share-link">{{ t('publish.shareLink') }}</router-link>
        </div>
        <p v-if="studentLink" class="row" data-testid="student-link">
          <code class="mono">{{ studentLink }}</code>
          <ActionButton action="copy-link" size="small" :label="t('invites.copy')" @click="copyStudentLink" />
        </p>
      </section>
      <ConfirmDialog v-model:visible="review" :title="t('publish.reviewTitle', { name: course?.title ?? '' })" action="publish" :label="t('publish.confirm')" :busy="busy" :error="error" :solid="false" @confirm="publish">
        <ul class="stack">
          <li>{{ t('publish.reviewStatus', { status: t(`course.status.${course?.status ?? 'draft'}`) }) }}</li>
          <li>{{ t('publish.questions', { count: t('count.question', validation?.blocking_questions.length ?? 0) }) }}</li>
          <li>{{ t('publish.reviewVisible') }}</li>
          <li v-if="(course?.translations ?? []).length > 0" data-testid="publish-translations">{{ t('publish.translationsKept', { languages: (course?.translations ?? []).map((x) => x.language).join(', ') }) }}</li>
          <li v-for="x in (course?.translations ?? []).filter((y) => y.status !== 'ready')" :key="x.language" class="danger-text" data-testid="publish-translation-warning">{{ t('publish.translationHeld', { language: x.language, held: x.held, failed: x.failed }) }}</li>
        </ul>
      </ConfirmDialog>
    </template>
  </CourseHeader>
</template>
