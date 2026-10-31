<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import { ApiError, request } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import type { CourseRow } from '@/api/types'
import ResourceState from '@/components/ResourceState.vue'
import { needsNow } from '@/composables/needsNow'
import { notify } from '@/composables/notify'
import { parseTags } from '@/composables/tags'
import { dropCached, useResource } from '@/composables/useResource'
import { useSessionStore } from '@/stores/session'

interface ListRow extends CourseRow { has_profile?: boolean; has_plan?: boolean; open_questions?: number }
const { t } = useI18n()
const router = useRouter()
const session = useSessionStore()
const courses = useResource<ListRow[]>('courses', (signal) => request<ListRow[]>(ENDPOINTS.courses, { signal }))

const open = ref(false)
const title = ref('')
const language = ref('en')
const tagsText = ref('')
const busy = ref(false)
const formError = ref<string | null>(null)
const languages = [{ code: 'en', label: 'English' }, { code: 'nb', label: 'Norsk bokmål' }, { code: 'uk', label: 'Українська' }]

function phrase(c: ListRow): string {
  return needsNow({ status: c.status, profile: c.has_profile ?? false, plan: c.has_plan ?? false, openQuestions: c.open_questions ?? 0, published: c.status === 'published' })
}

async function create(): Promise<void> {
  busy.value = true
  formError.value = null
  try {
    const made = await request<{ course_id: string }>(ENDPOINTS.courses, { method: 'POST', body: { title: title.value, language: language.value, scope_tags: parseTags(tagsText.value) } })
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
  <div class="row">
    <h1 class="page-title">{{ t('courses.title') }}</h1>
    <Button class="row-end" :label="t('courses.new')" :disabled="!session.can('course.create')" data-testid="course-new" @click="open = true" />
  </div>
  <p v-if="!session.can('course.create')" class="hint">{{ t('common.reason', { code: 'course.create' }) }}</p>
  <ResourceState :loading="courses.loading.value" :error="courses.error.value" :empty="(courses.data.value ?? []).length === 0" :empty-text="t('courses.empty')" @retry="courses.reload()">
    <ul class="plain-list" data-testid="course-list">
      <li v-for="c in courses.data.value" :key="c.id" class="bell-item row" data-testid="course-item">
        <router-link :to="`/courses/${c.id}/plan`">{{ c.title }}</router-link>
        <span class="chip">{{ t(`course.status.${c.status}`) }}</span>
        <span class="muted">{{ t('course.needsNow', { what: t(phrase(c)) }) }}</span>
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
      </div>
      <div class="field">
        <label for="c-tags">{{ t('courses.tags') }}</label>
        <InputText id="c-tags" v-model="tagsText" />
        <span class="hint">{{ t('common.tagsHint') }}</span>
      </div>
      <p v-if="formError" class="mono danger-text" role="alert" data-testid="form-error">{{ formError }}</p>
      <div class="row">
        <Button type="submit" :label="t('common.create')" :loading="busy" :aria-busy="busy" :disabled="title.trim().length === 0" data-testid="dialog-submit" />
        <Button type="button" :label="t('common.cancel')" severity="secondary" text @click="open = false" />
      </div>
    </form>
  </Dialog>
</template>
