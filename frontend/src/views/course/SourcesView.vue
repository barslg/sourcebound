<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import { ApiError, request } from '@/api/client'
import { courseSources, ENDPOINTS, sourceApprove, sourceReject } from '@/api/endpoints'
import type { SourceRow } from '@/api/types'
import CourseHeader from '@/components/CourseHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import { notify } from '@/composables/notify'
import { useResource } from '@/composables/useResource'
import { useSessionStore } from '@/stores/session'

const { t } = useI18n()
const route = useRoute()
const session = useSessionStore()
const courseId = computed(() => String(route.params.id))
const sources = useResource<SourceRow[]>(`sources:${courseId.value}`, (signal) => request<SourceRow[]>(courseSources(courseId.value), { signal }))

const media = ref('text')
const mediaOptions = computed(() => ['text', 'markdown', 'pdf', 'image', 'audio'].map((code) => ({ code, label: t(`sources.media.${code}`) })))
const licence = ref('')
const asOf = ref('')
const url = ref('')
const file = ref<File | null>(null)
const busy = ref<'upload' | 'fetch' | null>(null)
const formError = ref<string | null>(null)
const added = ref<Array<{ id: string; label: string; queued: boolean }>>([])
const working = ref<string | null>(null)

const candidates = computed(() => (sources.data.value ?? []).filter((s) => s.approval === 'pending'))
const library = computed(() => (sources.data.value ?? []).filter((s) => s.approval !== 'pending'))

function onFile(event: Event): void {
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null
}

async function upload(): Promise<void> {
  if (!file.value) return
  busy.value = 'upload'
  formError.value = null
  try {
    const form = new FormData()
    form.append('file', file.value)
    const query: Record<string, string> = { media: media.value }
    if (licence.value.trim()) query.licence = licence.value.trim()
    if (asOf.value.trim()) query.as_of = asOf.value.trim()
    const made = await request<{ source_id: string }>(ENDPOINTS.sourceUpload, { method: 'POST', body: form, query })
    added.value = [{ id: made.source_id, label: file.value.name, queued: false }, ...added.value]
    notify('success', t('sources.uploaded'))
    file.value = null
    await sources.reload(true)
  } catch (cause) {
    formError.value = cause instanceof ApiError ? cause.message : String(cause)
  } finally {
    busy.value = null
  }
}

async function addUrl(): Promise<void> {
  busy.value = 'fetch'
  formError.value = null
  try {
    const made = await request<{ source_id?: string; queued?: string }>(ENDPOINTS.sourceFetch, { method: 'POST', body: { url: url.value.trim(), course_id: courseId.value } })
    added.value = [{ id: made.source_id ?? made.queued ?? '', label: url.value.trim(), queued: made.queued !== undefined }, ...added.value]
    notify(made.queued ? 'info' : 'success', made.queued ? t('sources.queuedForHuman') : t('sources.fetched'))
    url.value = ''
    await sources.reload(true)
  } catch (cause) {
    formError.value = cause instanceof ApiError ? cause.message : String(cause)
  } finally {
    busy.value = null
  }
}

async function decide(source: SourceRow, verdict: 'approve' | 'reject'): Promise<void> {
  working.value = source.id
  try {
    await request(verdict === 'approve' ? sourceApprove(source.id) : sourceReject(source.id), { method: 'POST' })
    notify('success', verdict === 'approve' ? t('sources.approved') : t('sources.rejected'))
    await sources.reload(true)
  } catch (cause) {
    notify('error', cause instanceof ApiError ? cause.message : String(cause))
  } finally {
    working.value = null
  }
}
</script>

<template>
  <CourseHeader :course-id="courseId">
    <section class="stack">
      <h2>{{ t('sources.add') }}</h2>
      <p class="hint">{{ t('sources.addHint') }}</p>
      <form class="stack" @submit.prevent="upload">
        <div class="field">
          <label for="s-file">{{ t('sources.file') }}</label>
          <input id="s-file" type="file" data-testid="source-file" @change="onFile" />
        </div>
        <div class="field">
          <label for="s-media">{{ t('sources.mediaLabel') }}</label>
          <Select v-model="media" input-id="s-media" :options="mediaOptions" option-label="label" option-value="code" />
        </div>
        <div class="field">
          <label for="s-lic">{{ t('sources.licence') }}</label>
          <InputText id="s-lic" v-model="licence" />
          <span class="hint">{{ t('sources.licenceHint') }}</span>
        </div>
        <div class="field">
          <label for="s-asof">{{ t('sources.asOf') }}</label>
          <InputText id="s-asof" v-model="asOf" />
          <span class="hint">{{ t('sources.asOfHint') }}</span>
        </div>
        <div class="row">
          <Button type="submit" :label="t('sources.upload')" :loading="busy === 'upload'" :aria-busy="busy === 'upload'" :disabled="!file || !session.can('source.create')" data-testid="source-upload" />
        </div>
      </form>
      <form class="stack" @submit.prevent="addUrl">
        <div class="field">
          <label for="s-url">{{ t('sources.url') }}</label>
          <InputText id="s-url" v-model="url" />
          <span class="hint">{{ t('sources.urlHint') }}</span>
        </div>
        <div class="row">
          <Button type="submit" :label="t('sources.fetch')" :loading="busy === 'fetch'" :aria-busy="busy === 'fetch'" :disabled="url.trim().length === 0 || !session.can('source.create')" data-testid="source-fetch" />
        </div>
      </form>
      <p v-if="formError" class="mono danger-text" role="alert" data-testid="form-error">{{ formError }}</p>
      <ul v-if="added.length > 0" class="plain-list" data-testid="added-list">
        <li v-for="item in added" :key="item.id" class="bell-item row">
          <span>{{ item.label }}</span>
          <span class="chip mono">{{ item.id }}</span>
          <span v-if="item.queued" class="chip chip-warn">{{ t('sources.waitsForHuman') }}</span>
        </li>
      </ul>
    </section>
    <section class="stack">
      <h2>{{ t('sources.candidates') }}</h2>
      <ResourceState :loading="sources.loading.value" :error="sources.error.value" :empty="(sources.data.value ?? []).length === 0" :empty-text="t('sources.empty')" @retry="sources.reload()">
        <ul class="plain-list" data-testid="candidate-list">
          <li v-for="s in candidates" :key="s.id" class="bell-item stack" data-testid="candidate">
            <div class="row"><strong>{{ s.title }}</strong><span class="chip">{{ s.media }}</span><span class="chip mono">{{ s.licence || t('sources.noLicence') }}</span></div>
            <div class="row">
              <Button :label="t('sources.approve')" size="small" :loading="working === s.id" :aria-busy="working === s.id" :disabled="!session.can('source.approve')" @click="decide(s, 'approve')" />
              <Button :label="t('sources.reject')" size="small" severity="danger" outlined :disabled="!session.can('source.approve') || working === s.id" @click="decide(s, 'reject')" />
            </div>
          </li>
        </ul>
        <h2>{{ t('sources.library') }}</h2>
        <p v-if="library.length === 0" class="muted">{{ t('sources.libraryEmpty') }}</p>
        <ul class="plain-list" data-testid="source-list">
          <li v-for="s in library" :key="s.id" class="bell-item stack">
            <div class="row"><strong>{{ s.title }}</strong><span class="chip">{{ s.media }}</span><span class="chip mono">{{ s.licence || t('sources.noLicence') }}</span><span v-if="s.as_of" class="muted">{{ t('sources.asOfOn', { date: s.as_of }) }}</span></div>
            <progress v-if="s.progress !== undefined && s.progress < 100" :value="s.progress" max="100" :aria-label="t('sources.progress')"></progress>
          </li>
        </ul>
      </ResourceState>
    </section>
  </CourseHeader>
</template>
