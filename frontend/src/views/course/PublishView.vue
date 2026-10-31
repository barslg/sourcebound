<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import { ApiError, request } from '@/api/client'
import { coursePublish } from '@/api/endpoints'
import CourseHeader from '@/components/CourseHeader.vue'
import { notify } from '@/composables/notify'
import { dropCached } from '@/composables/useResource'
import { useSessionStore } from '@/stores/session'
import InvitesView from '@/views/InvitesView.vue'

const { t } = useI18n()
const route = useRoute()
const session = useSessionStore()
const courseId = computed(() => String(route.params.id))
const header = ref<InstanceType<typeof CourseHeader> | null>(null)
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
  <CourseHeader ref="header" :course-id="courseId">
    <template #default="{ course, validation }">
      <section class="stack">
        <h2>{{ t('publish.title') }}</h2>
        <p v-if="course?.published_version_id" class="muted" data-testid="published-note">{{ t('publish.published', { version: course.published_version_id }) }}</p>
        <div class="row">
          <Button :label="t('publish.button')" :disabled="!session.can('course.publish')" data-testid="publish-open" @click="review = true" />
          <span v-if="!session.can('course.publish')" class="hint">{{ t('common.reason', { code: 'course.publish' }) }}</span>
        </div>
      </section>
      <InvitesView embedded :preset-tags="course?.scope_tags ?? []" />
      <Dialog v-model:visible="review" modal :header="t('publish.reviewTitle', { name: course?.title ?? '' })" :style="{ width: 'min(520px, 92vw)' }">
        <ul class="stack">
          <li>{{ t('publish.reviewStatus', { status: t(`course.status.${course?.status ?? 'draft'}`) }) }}</li>
          <li>{{ t('publish.reviewQuestions', { count: validation?.blocking_questions.length ?? 0 }) }}</li>
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
