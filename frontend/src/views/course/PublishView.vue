<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { ApiError, request } from '@/api/client'
import { coursePlan, coursePublish } from '@/api/endpoints'
import CourseHeader from '@/components/CourseHeader.vue'
import ActionButton from '@/components/ActionButton.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import Section from '@/components/Section.vue'
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
        <Section id="summary" :title="t('publish.summary')" :help="t('publish.summaryHelp')" />
        <ul class="plain-list" data-testid="publish-summary">
          <li>{{ t('publish.reviewStatus', { status: t(`course.status.${course?.status ?? 'draft'}`) }) }}</li>
          <li>{{ t('publish.topics', { count: t('count.topic', topics) }) }}</li>
          <li>{{ t('publish.held', { count: t('count.block', heldCount) }) }} <router-link v-if="heldCount > 0" :to="`/courses/${courseId}/fix#held-list`">{{ t('publish.fixThem') }}</router-link></li>
          <li>{{ t('publish.questions', { count: t('count.question', validation?.blocking_questions.length ?? 0) }) }}</li>
        </ul>
        <p v-if="course?.published_version_id" class="muted" data-testid="published-note">{{ t('publish.published') }}</p>
        <Section id="publish" :title="t('publish.title')" :help="t('publish.help')" />
        <div class="row">
          <ActionButton action="publish" :label="t('publish.button')" :reason="firstReason(lacking('course.publish'), heldCount > 0 && t('publish.heldFirst', { count: t('count.block', heldCount) }))" data-testid="publish-open" @click="review = true" />
          <span id="publish-open" />
        </div>
        <Section :title="t('publish.share')" :help="t('publish.shareHelp')" />
        <router-link :to="`/invites?course=${courseId}`" data-testid="share-link">{{ t('publish.shareLink') }}</router-link>
      </section>
      <ConfirmDialog v-model:visible="review" :title="t('publish.reviewTitle', { name: course?.title ?? '' })" action="publish" :label="t('publish.confirm')" :busy="busy" :error="error" :solid="false" @confirm="publish">
        <ul class="stack">
          <li>{{ t('publish.reviewStatus', { status: t(`course.status.${course?.status ?? 'draft'}`) }) }}</li>
          <li>{{ t('publish.questions', { count: t('count.question', validation?.blocking_questions.length ?? 0) }) }}</li>
          <li>{{ t('publish.reviewVisible') }}</li>
        </ul>
      </ConfirmDialog>
    </template>
  </CourseHeader>
</template>
