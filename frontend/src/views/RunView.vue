<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import Button from 'primevue/button'
import { ApiError, request } from '@/api/client'
import { runCancel, runItem, runResume } from '@/api/endpoints'
import ResourceState from '@/components/ResourceState.vue'
import { notify } from '@/composables/notify'
import { stepWord, stoppedLine, type RunDoc } from '@/composables/runView'
import { useResource } from '@/composables/useResource'
import { useSessionStore } from '@/stores/session'

const { t } = useI18n()
const route = useRoute()
const session = useSessionStore()
const runId = computed(() => String(route.params.runId))
const run = useResource<RunDoc>(`run:${runId.value}`, (signal) => request<RunDoc>(runItem(runId.value), { signal }))
const working = ref<'resume' | 'cancel' | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

const doc = computed(() => run.data.value)
const active = computed(() => doc.value?.steps.find((s) => s.status === 'running') ?? null)
const line = computed(() => {
  const d = doc.value
  if (!d) return ''
  const why = stoppedLine(d)
  if (why) return t('run.lineStopped', { why })
  if (d.status === 'done') return t('run.lineDone')
  return active.value ? t('run.lineStep', { step: active.value.step_key }) : t('run.lineWaiting')
})

onMounted(() => { timer = setInterval(() => { if (doc.value && doc.value.status !== 'done') void run.reload(true) }, 3000) })
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

async function act(kind: 'resume' | 'cancel'): Promise<void> {
  working.value = kind
  try {
    await request(kind === 'resume' ? runResume(runId.value) : runCancel(runId.value), { method: 'POST' })
    notify('success', kind === 'resume' ? t('run.resumed') : t('run.cancelled'))
    await run.reload(true)
  } catch (cause) {
    notify('error', cause instanceof ApiError ? cause.message : String(cause))
  } finally {
    working.value = null
  }
}
</script>

<template>
  <h1 class="page-title">{{ t('run.title') }}</h1>
  <ResourceState :loading="run.loading.value" :error="run.error.value" :empty="doc === null" :empty-text="t('run.empty')" @retry="run.reload()">
    <div class="stack" data-testid="run">
      <div class="row">
        <span class="chip" data-testid="run-status">{{ t(`run.status.${doc?.status ?? 'running'}`) }}</span>
        <span data-testid="run-line">{{ line }}</span>
      </div>
      <p data-testid="run-cost">{{ t('run.cost', { spent: t('run.spentUnknown'), forecast: doc?.estimate?.cost_usd ?? t('run.noForecast') }) }}</p>
      <p class="hint">{{ t('plan.forecastNote') }}</p>
      <ul class="plain-list">
        <li v-for="s in doc?.steps ?? []" :key="s.step_key" class="bell-item row" data-testid="run-step">
          <span class="mono">{{ s.step_key }}</span>
          <span class="chip" :class="{ 'chip-warn': stepWord(s) === 'held' || stepWord(s) === 'stopped' }">{{ t(`run.word.${stepWord(s)}`, stepWord(s)) }}</span>
          <span v-if="s.error" class="mono danger-text">{{ s.error }}</span>
        </li>
      </ul>
      <div class="row">
        <Button :label="t('run.resume')" :loading="working === 'resume'" :aria-busy="working === 'resume'" :disabled="doc?.status !== 'stopped' || !session.can('run.retry')" data-testid="run-resume" @click="act('resume')" />
        <Button :label="t('run.cancel')" severity="danger" outlined :loading="working === 'cancel'" :aria-busy="working === 'cancel'" :disabled="doc?.status !== 'running' || !session.can('run.cancel')" data-testid="run-cancel" @click="act('cancel')" />
      </div>
      <p class="hint">{{ t('run.resumeHint') }}</p>
    </div>
  </ResourceState>
</template>
