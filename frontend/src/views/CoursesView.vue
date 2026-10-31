<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import { ApiError, request } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import type { CourseRow } from '@/api/types'
import GuardedButton from '@/components/GuardedButton.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import { nextAction } from '@/composables/needsNow'
import { notify } from '@/composables/notify'
import { lacking } from '@/composables/reasons'
import { dropCached, useResource } from '@/composables/useResource'

interface ListRow extends CourseRow { has_profile?: boolean; has_plan?: boolean; open_questions?: number }
const { t } = useI18n()
const router = useRouter()
const courses = useResource<ListRow[]>('courses', (signal) => request<ListRow[]>(ENDPOINTS.courses, { signal }))

const open = ref(false)
const title = ref('')
const language = ref('en')
const busy = ref(false)
const formError = ref<string | null>(null)
const languages = [{ code: 'en', label: 'English' }, { code: 'nb', label: 'Norsk bokmål' }, { code: 'uk', label: 'Українська' }]
const total = computed(() => (courses.data.value ?? []).length)
const action = (c: ListRow) => nextAction({
  courseId: c.id, status: c.status, profile: c.has_profile ?? false, plan: c.has_plan ?? false, openQuestions: c.open_questions ?? 0,
  published: c.status === 'published', firstQuestionKey: null, pendingSources: 0, firstPendingId: null, held: 0, firstHeldId: null, generated: false,
})

async function create(): Promise<void> {
  busy.value = true
  formError.value = null
  try {
    const made = await request<{ course_id: string }>(ENDPOINTS.courses, { method: 'POST', body: { title: title.value, language: language.value, scope_tags: [`c-${crypto.randomUUID().slice(0, 8)}`] } })
    notify('success', t('courses.created'))
    dropCached('courses')
    open.value = false
    title.value = ''
    await router.push(`/courses/${made.course_id}/plan`)
  } catch (cause) {
    formError.value = cause instanceof ApiError ? cause.message : String(cause)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <PageHeader :title="t('courses.title')" :help="t('courses.help')">
    <template #actions><GuardedButton :label="t('courses.new')" :reason="lacking('course.create')" data-testid="course-new" @click="open = true" /></template>
  </PageHeader>
  <ResourceState :loading="courses.loading.value" :error="courses.error.value" :empty="total === 0" :empty-text="t('courses.empty')" @retry="courses.reload()">
    <template #empty-actions><Button :label="t('courses.first')" data-testid="course-first" @click="open = true" /></template>
    <p class="muted" data-testid="course-count">{{ t('courses.count', { count: t('count.course', total) }) }}</p>
    <ul class="plain-list" data-testid="course-list">
      <li v-for="c in courses.data.value" :key="c.id" class="bell-item row" data-testid="course-item">
        <router-link :to="`/courses/${c.id}/plan`">{{ c.title }}</router-link>
        <span class="chip">{{ t(`course.status.${c.status}`) }}</span>
        <span class="muted" data-testid="needs-now">
          {{ t('course.needsNowLabel') }}
          <router-link v-if="action(c).to" :to="action(c).to as string" data-testid="needs-link">{{ t(action(c).key) }}</router-link>
          <template v-else>{{ t(action(c).key) }}</template>
        </span>
      </li>
    </ul>
  </ResourceState>
  <Dialog v-model:visible="open" modal :header="t('courses.newTitle')" :style="{ width: 'min(480px, 92vw)' }">
    <form class="stack" @submit.prevent="create">
      <div class="field">
        <label for="c-title">{{ t('courses.titleLabel') }}</label>
        <InputText id="c-title" v-model="title" />
        <span class="hint">{{ t('courses.titleHint') }}</span>
      </div>
      <div class="field">
        <label for="c-lang">{{ t('courses.language') }}</label>
        <Select v-model="language" input-id="c-lang" :options="languages" option-label="label" option-value="code" />
        <span class="hint">{{ t('courses.languageHint') }}</span>
      </div>
      <p v-if="formError" class="mono danger-text" role="alert" data-testid="form-error">{{ formError }}</p>
      <div class="row">
        <GuardedButton type="submit" :label="t('courses.createButton')" :loading="busy" :reason="title.trim().length === 0 ? t('courses.typeTitle') : null" data-testid="dialog-submit" />
        <Button type="button" :label="t('common.cancel')" severity="secondary" text @click="open = false" />
      </div>
    </form>
  </Dialog>
</template>
