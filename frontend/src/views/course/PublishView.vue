<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import { ApiError, request } from '@/api/client'
import { coursePlan, coursePublish } from '@/api/endpoints'
import CourseHeader from '@/components/CourseHeader.vue'
import GuardedButton from '@/components/GuardedButton.vue'
import SectionHeading from '@/components/SectionHeading.vue'
import { firstReason, lacking } from '@/composables/reasons'
import { notify } from '@/composables/notify'
import type { PlanTree } from '@/composables/planTree'
import { dropCached, useResource } from '@/composables/useResource'

const { t } = useI18n()
const route = useRoute()
const courseId = computed(() => String(route.params.id))
const header = ref<InstanceType<typeof CourseHeader> | null>(null)
const plan = useResource<PlanTree>(`plan:${courseId.value}`, (signal) => request<PlanTree>(coursePlan(courseId.value), { signal }))
const topics = computed(() => (plan.data.value?.modules ?? []).reduce((n, m) => n + m.topics.length, 0))
const review = ref(false)
const busy = ref(false)
const error = ref<string | null>(null)

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
  <CourseHeader ref="header" :course-id="courseId" page="publish">
    <template #default="{ course, validation, heldCount }">
      <section class="stack">
        <SectionHeading id="summary" :title="t('publish.summary')" :help="t('publish.summaryHelp')" />
        <ul class="plain-list" data-testid="publish-summary">
          <li>{{ t('publish.reviewStatus', { status: t(`course.status.${course?.status ?? 'draft'}`) }) }}</li>
          <li>{{ t('publish.topics', { count: t('count.topic', topics) }) }}</li>
          <li>{{ t('publish.held', { count: t('count.block', heldCount) }) }} <router-link v-if="heldCount > 0" :to="`/courses/${courseId}/fix#held-list`">{{ t('publish.fixThem') }}</router-link></li>
          <li>{{ t('publish.questions', { count: t('count.question', validation?.blocking_questions.length ?? 0) }) }}</li>
        </ul>
        <p v-if="course?.published_version_id" class="muted" data-testid="published-note">{{ t('publish.published') }}</p>
        <SectionHeading id="publish" :title="t('publish.title')" :help="t('publish.help')" />
        <div class="row">
          <GuardedButton :label="t('publish.button')" :reason="firstReason(lacking('course.publish'), heldCount > 0 && t('publish.heldFirst', { count: t('count.block', heldCount) }))" data-testid="publish-open" @click="review = true" />
          <span id="publish-open" />
        </div>
        <SectionHeading :title="t('publish.share')" :help="t('publish.shareHelp')" />
        <router-link :to="`/invites?course=${courseId}`" data-testid="share-link">{{ t('publish.shareLink') }}</router-link>
      </section>
      <Dialog v-model:visible="review" modal :header="t('publish.reviewTitle', { name: course?.title ?? '' })" :style="{ width: 'min(520px, 92vw)' }">
        <ul class="stack">
          <li>{{ t('publish.reviewStatus', { status: t(`course.status.${course?.status ?? 'draft'}`) }) }}</li>
          <li>{{ t('publish.questions', { count: t('count.question', validation?.blocking_questions.length ?? 0) }) }}</li>
          <li>{{ t('publish.reviewVisible') }}</li>
        </ul>
        <p v-if="error" class="mono danger-text" role="alert" data-testid="form-error">{{ error }}</p>
        <div class="row">
          <Button :label="t('publish.confirm')" :loading="busy" :aria-busy="busy" data-testid="dialog-submit" @click="publish" />
          <Button :label="t('common.cancel')" severity="secondary" text @click="review = false" />
        </div>
      </Dialog>
    </template>
  </CourseHeader>
</template>
