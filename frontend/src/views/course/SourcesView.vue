<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import Button from 'primevue/button'
import Drawer from 'primevue/drawer'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import { ApiError, request } from '@/api/client'
import { courseSources, ENDPOINTS, humanQueue, humanQueueResolve, sourceApprove, sourceReject } from '@/api/endpoints'
import type { QueueItem, SourceRow } from '@/api/types'
import CourseHeader from '@/components/CourseHeader.vue'
import GuardedButton from '@/components/GuardedButton.vue'
import ResourceState from '@/components/ResourceState.vue'
import SectionHeading from '@/components/SectionHeading.vue'
import { humanize } from '@/composables/labels'
import { firstReason, lacking } from '@/composables/reasons'
import { notify } from '@/composables/notify'
import { sourceCount } from '@/composables/sourceView'
import { useResource } from '@/composables/useResource'

const { t, te } = useI18n()
const route = useRoute()
const courseId = computed(() => String(route.params.id))
const header = ref<InstanceType<typeof CourseHeader> | null>(null)
const sources = useResource<SourceRow[]>(`sources:${courseId.value}`, (signal) => request<SourceRow[]>(courseSources(courseId.value), { signal }))
const queue = useResource<QueueItem[]>(`queue:${courseId.value}`, (signal) => request<QueueItem[]>(humanQueue, { signal }))
const waiting = computed(() => (queue.data.value ?? []).filter((q) => q.payload?.course_id === courseId.value))

const drawer = ref(false)
const media = ref('text')
const licence = ref('')
const asOf = ref('')
const url = ref('')
const file = ref<File | null>(null)
const busy = ref<'upload' | 'fetch' | null>(null)
const formError = ref<string | null>(null)
const added = ref<Array<{ id: string; label: string; queued: boolean }>>([])
const working = ref<string | null>(null)
const mediaOptions = computed(() => ['text', 'markdown', 'pdf', 'image', 'audio'].map((code) => ({ code, label: t(`sources.media.${code}`) })))

const candidates = computed(() => (sources.data.value ?? []).filter((s) => s.approval === 'pending'))
const library = computed(() => (sources.data.value ?? []).filter((s) => s.approval !== 'pending'))
const mediaLabel = (code: string): string => (te(`sources.media.${code}`) ? t(`sources.media.${code}`) : humanize(code))
const ingestLabel = (s: SourceRow): string => (s.ingest_status && te(`sources.ingest.${s.ingest_status}`) ? t(`sources.ingest.${s.ingest_status}`) : '')

const KINDS: Array<[RegExp, string]> = [[/\.(md|markdown)$/i, 'markdown'], [/\.pdf$/i, 'pdf'], [/\.(png|jpe?g|webp|gif)$/i, 'image'], [/\.(mp3|wav|m4a|ogg|flac)$/i, 'audio']]
function onFile(event: Event): void {
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null
  if (file.value) media.value = KINDS.find(([re]) => re.test(file.value?.name ?? ''))?.[1] ?? 'text'
}
async function refreshAll(): Promise<void> {
  await Promise.all([sources.reload(true), queue.reload(true), header.value?.reload()])
}

async function upload(): Promise<void> {
  if (!file.value) return
  busy.value = 'upload'
  formError.value = null
  try {
    const form = new FormData()
    form.append('file', file.value)
    const query: Record<string, string> = { media: media.value, course_id: courseId.value }
    if (licence.value.trim()) query.licence = licence.value.trim()
    if (asOf.value.trim()) query.as_of = asOf.value.trim()
    const made = await request<{ source_id: string }>(ENDPOINTS.sourceUpload, { method: 'POST', body: form, query })
    added.value = [{ id: made.source_id, label: file.value.name, queued: false }, ...added.value]
    notify('success', t('sources.uploaded'))
    file.value = null
    drawer.value = false
    await refreshAll()
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
    drawer.value = false
    await refreshAll()
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
    await refreshAll()
  } catch (cause) {
    notify('error', cause instanceof ApiError ? cause.message : String(cause))
  } finally {
    working.value = null
  }
}

async function resolve(item: QueueItem, action: 'approve' | 'dismiss'): Promise<void> {
  working.value = item.id
  try {
    await request(humanQueueResolve(item.id), { method: 'POST', body: { action } })
    notify('success', action === 'approve' ? t('sources.addressApproved') : t('sources.addressDismissed'))
    await refreshAll()
  } catch (cause) {
    notify('error', cause instanceof ApiError ? cause.message : String(cause))
  } finally {
    working.value = null
  }
}
</script>

<template>
  <CourseHeader ref="header" :course-id="courseId" page="sources">
    <div class="row">
      <GuardedButton class="row-end" :label="t('sources.add')" :reason="lacking('source.upload')" data-testid="source-add" @click="drawer = true" />
    </div>
    <div id="sources-list" class="stack">
      <section v-if="waiting.length > 0" class="stack" data-testid="human-queue">
        <SectionHeading :title="t('sources.waitingAddresses')" :help="t('sources.waitingAddressesHelp')" />
        <ul class="plain-list">
          <li v-for="q in waiting" :key="q.id" class="bell-item stack" data-testid="queued-address">
            <span class="mono">{{ q.url }}</span>
            <span class="muted">{{ q.reason }}</span>
            <div class="row">
              <GuardedButton :label="t('sources.approveAddress')" size="small" :loading="working === q.id" :reason="lacking('source.approve')" @click="resolve(q, 'approve')" />
              <GuardedButton :label="t('sources.dismissAddress')" size="small" severity="secondary" :reason="lacking('source.approve')" @click="resolve(q, 'dismiss')" />
            </div>
          </li>
        </ul>
      </section>
      <ResourceState :loading="sources.loading.value" :error="sources.error.value" :empty="(sources.data.value ?? []).length === 0" :empty-text="t('sources.empty')" @retry="sources.reload()">
        <template #empty-actions><Button :label="t('sources.addFirst')" data-testid="source-add-empty" @click="drawer = true" /></template>
        <section class="stack">
          <SectionHeading :title="t('sources.candidates')" :help="t('sources.candidatesHelp')" />
          <p v-if="candidates.length === 0" class="muted">{{ t('sources.noCandidates') }}</p>
          <ul class="plain-list" data-testid="candidate-list">
            <li v-for="s in candidates" :id="`source-${s.id}`" :key="s.id" class="bell-item stack" data-testid="candidate">
              <div class="row"><strong>{{ s.title }}</strong><span class="chip">{{ mediaLabel(s.media) }}</span><span class="chip">{{ s.licence || t('sources.noLicence') }}</span><span v-if="ingestLabel(s)" class="chip" :class="{ 'chip-warn': s.ingest_status === 'failed' }">{{ ingestLabel(s) }}</span></div>
              <span class="muted">{{ t(sourceCount(s.progress).key, s.progress) }}</span>
              <span v-if="s.ingest_message" class="mono danger-text">{{ s.ingest_message }}</span>
              <div class="row">
                <GuardedButton :label="t('sources.approve')" size="small" :loading="working === s.id" :reason="lacking('source.approve')" @click="decide(s, 'approve')" />
                <GuardedButton :label="t('sources.reject')" size="small" severity="danger" outlined :reason="lacking('source.approve')" @click="decide(s, 'reject')" />
              </div>
            </li>
          </ul>
        </section>
        <section class="stack">
          <SectionHeading :title="t('sources.library')" :help="t('sources.libraryHelp')" />
          <p v-if="library.length === 0" class="muted">{{ t('sources.libraryEmpty') }}</p>
          <ul class="plain-list" data-testid="source-list">
            <li v-for="s in library" :id="`source-${s.id}`" :key="s.id" class="bell-item stack">
              <div class="row"><strong>{{ s.title }}</strong><span class="chip">{{ mediaLabel(s.media) }}</span><span class="chip">{{ s.licence || t('sources.noLicence') }}</span><span v-if="s.as_of" class="muted">{{ t('sources.asOfOn', { date: s.as_of }) }}</span><span class="chip" :class="{ 'chip-warn': s.approval === 'held' }" data-testid="source-state">{{ t(`sources.state.${s.approval}`, s.approval) }}</span><span v-if="ingestLabel(s)" class="chip" :class="{ 'chip-warn': s.ingest_status === 'failed' }">{{ ingestLabel(s) }}</span></div>
              <span class="muted" data-testid="source-passages">{{ t(sourceCount(s.progress).key, s.progress) }}</span>
              <span v-if="s.ingest_message" class="mono danger-text">{{ s.ingest_message }}</span>
            </li>
          </ul>
        </section>
      </ResourceState>
    </div>
    <ul v-if="added.length > 0" class="plain-list" data-testid="added-list">
      <li v-for="item in added" :key="item.id" class="bell-item row">
        <router-link v-if="!item.queued" :to="`#source-${item.id}`">{{ item.label }}</router-link>
        <span v-else>{{ item.label }}</span>
        <span v-if="item.queued" class="chip chip-warn">{{ t('sources.waitsForHuman') }}</span>
      </li>
    </ul>
    <Drawer v-model:visible="drawer" position="right" :header="t('sources.add')" :style="{ width: 'min(480px, 96vw)' }">
      <div id="add-source" class="stack">
        <p class="hint" data-testid="section-help">{{ t('sources.addHint') }}</p>
        <form class="stack" @submit.prevent="upload">
          <SectionHeading :title="t('sources.fromFile')" :help="t('sources.fromFileHelp')" />
          <div class="field">
            <label for="s-file">{{ t('sources.file') }}</label>
            <input id="s-file" type="file" data-testid="source-file" @change="onFile" />
            <span class="hint">{{ t('sources.fileHint') }}</span>
          </div>
          <div class="field">
            <label for="s-media">{{ t('sources.mediaLabel') }}</label>
            <Select v-model="media" input-id="s-media" :options="mediaOptions" option-label="label" option-value="code" />
            <span class="hint">{{ t('sources.mediaHint') }}</span>
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
          <GuardedButton type="submit" :label="t('sources.upload')" :loading="busy === 'upload'" :reason="firstReason(lacking('source.upload'), !file && t('sources.chooseFile'))" data-testid="source-upload" />
        </form>
        <p class="muted" data-testid="or-line">{{ t('sources.or') }}</p>
        <form class="stack" @submit.prevent="addUrl">
          <SectionHeading :title="t('sources.fromAddress')" :help="t('sources.fromAddressHelp')" />
          <div class="field">
            <label for="s-url">{{ t('sources.url') }}</label>
            <InputText id="s-url" v-model="url" />
            <span class="hint">{{ t('sources.urlHint') }}</span>
          </div>
          <GuardedButton type="submit" :label="t('sources.fetch')" :loading="busy === 'fetch'" :reason="firstReason(lacking('source.upload'), url.trim().length === 0 && t('sources.typeAddress'))" data-testid="source-fetch" />
        </form>
        <p v-if="formError" class="mono danger-text" role="alert" data-testid="form-error">{{ formError }}</p>
      </div>
    </Drawer>
  </CourseHeader>
</template>
