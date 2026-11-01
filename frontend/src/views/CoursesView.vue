<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import { ApiError, request } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import type { CourseRow } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import AppDialog from '@/components/AppDialog.vue'
import FormField from '@/components/FormField.vue'
import StatusChip from '@/components/StatusChip.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import { factsOfRow, nextAction } from '@/composables/needsNow'
import { notify } from '@/composables/notify'
import { lacking } from '@/composables/reasons'
import { dropCached, useResource } from '@/composables/useResource'

const { t } = useI18n()
const router = useRouter()
const courses = useResource<CourseRow[]>('courses', (signal) => request<CourseRow[]>(ENDPOINTS.courses, { signal }))

const open = ref(false)
const title = ref('')
const language = ref('en')
const busy = ref(false)
const formError = ref<string | null>(null)
const languages = [{ code: 'en', label: 'English' }, { code: 'nb', label: 'Norsk bokmål' }, { code: 'uk', label: 'Українська' }]
const total = computed(() => (courses.data.value ?? []).length)
const action = (c: CourseRow) => nextAction(factsOfRow(c))

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
  <PageHeader>
    <template #actions><ActionButton accent action="create" :label="t('courses.new')" :reason="lacking('course.create')" data-testid="course-new" @click="open = true" /></template>
  </PageHeader>
  <ResourceState :loading="courses.loading.value" :error="courses.error.value" :empty="total === 0" :empty-text="t('courses.empty')" @retry="courses.reload()">
    <template #empty-actions><ActionButton action="create" :label="t('courses.first')" data-testid="course-first" @click="open = true" /></template>
    <p class="muted" data-testid="course-count">{{ t('courses.count', { count: t('count.course', total) }) }}</p>
    <ul class="plain-list" data-testid="course-list">
      <li v-for="c in courses.data.value" :key="c.id" class="bell-item course-row" data-testid="course-item">
        <div>
          <router-link class="course-row-title" :to="`/courses/${c.id}/plan`">{{ c.title }}</router-link>
          <div class="course-row-meta">
            <StatusChip :tone="c.status === 'published' ? 'ok' : c.status === 'generated' ? 'info' : 'neutral'" :label="t(`course.status.${c.status}`)" />
            <span v-if="c.held_count > 0" class="muted" data-testid="held-count">{{ t('courses.held', { count: t('count.block', c.held_count) }) }}</span>
          </div>
        </div>
        <span class="muted course-row-next" data-testid="needs-now">
          {{ t('course.needsNowLabel') }}
          <router-link v-if="action(c).to" class="link-button" :to="action(c).to as string" data-testid="needs-link">{{ t(action(c).key, action(c).params ?? {}) }}</router-link>
          <template v-else>{{ t(action(c).key, action(c).params ?? {}) }}</template>
        </span>
      </li>
    </ul>
  </ResourceState>
  <AppDialog v-model:visible="open" :header="t('courses.newTitle')">
    <form class="stack" @submit.prevent="create">
      <FormField id="c-title" :label="t('courses.titleLabel')" :help="t('courses.titleHint')" required :error="title.trim().length === 0 ? t('form.isRequired') : null">
        <InputText id="c-title" v-model="title" />
      </FormField>
      <FormField id="c-lang" :label="t('courses.language')" :help="t('courses.languageHint')">
        <Select v-model="language" input-id="c-lang" :options="languages" option-label="label" option-value="code" />
      </FormField>
      <p v-if="formError" class="mono danger-text" role="alert" data-testid="form-error">{{ formError }}</p>
      <div class="row">
        <ActionButton action="cancel" @click="open = false" />
        <ActionButton type="submit" class="row-end" action="create" :label="t('courses.createButton')" :loading="busy" :reason="title.trim().length === 0 ? t('courses.typeTitle') : null" data-testid="dialog-submit" />
      </div>
    </form>
  </AppDialog>
</template>
