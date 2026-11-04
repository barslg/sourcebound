<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import InputText from 'primevue/inputtext'
import { ApiError, request } from '@/api/client'
import { courseBuild, runCancel } from '@/api/endpoints'
import type { BuildReply } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import FormField from '@/components/FormField.vue'
import StatusChip from '@/components/StatusChip.vue'
import { usd } from '@/composables/labels'
import { notify, notifyError } from '@/composables/notify'
import { firstReason, lacking } from '@/composables/reasons'
import { dropCached, useResource } from '@/composables/useResource'
import { useSessionStore } from '@/stores/session'

const props = defineProps<{ courseId: string; draftCount: number; blockCount: number; runGoing: boolean }>()
const emit = defineEmits<{ changed: [] }>()
const { t } = useI18n()
const session = useSessionStore()
const noKey = computed(() => (session.me?.model_key_ready ? null : t('account.noKey')))

const res = useResource<BuildReply>(`build:${props.courseId}`, (signal) => request<BuildReply>(courseBuild(props.courseId), { signal }))
const build = computed(() => res.data.value?.build ?? null)
const state = computed(() => build.value?.state ?? null)

let timer: ReturnType<typeof setInterval> | null = null
function stopPolling(): void { if (timer) { clearInterval(timer); timer = null } }
watch(state, (now, before) => {
  stopPolling()
  if (now === 'building') timer = setInterval(() => { void res.reload(true) }, 3000)
  if (before === 'building' && now !== 'building') emit('changed')
}, { immediate: true })
onScopeDispose(stopPolling)

const capText = ref('15')
const capValue = computed(() => Number(capText.value))
const capBad = computed(() => !Number.isFinite(capValue.value) || capValue.value <= 0)
const working = ref(false)

async function reload(): Promise<void> {
  dropCached(`build:${props.courseId}`)
  await res.reload(true)
}
defineExpose({ reload })

async function post(continueBuild: boolean): Promise<void> {
  working.value = true
  try {
    await request(courseBuild(props.courseId), { method: 'POST', body: { queries: [], cap_usd: capText.value.trim(), continue: continueBuild, idempotency_key: crypto.randomUUID() } })
    notify('success', t('build.confirmed'))
    await reload()
    emit('changed')
  } catch (cause) {
    notifyError(cause instanceof ApiError ? cause : cause)
  } finally {
    working.value = false
  }
}

const confirmStop = ref(false)
async function stopBuild(): Promise<void> {
  const run = build.value?.current_run_id
  confirmStop.value = false
  if (!run) return
  working.value = true
  try {
    await request(runCancel(run), { method: 'POST' })
    notify('success', t('build.stopRequested'))
    await reload()
  } catch (cause) {
    notifyError(cause)
  } finally {
    working.value = false
  }
}

const startReason = computed(() => firstReason(noKey.value, lacking('course.plan'), lacking('course.generate'), props.runGoing && t('build.runGoing'), capBad.value && t('build.capInvalid')))
const continueReason = computed(() => firstReason(noKey.value, lacking('course.plan'), lacking('course.generate'), props.runGoing && t('build.runGoing'), capBad.value && t('build.capInvalid')))
const stopReason = computed(() => firstReason(lacking('run.cancel'), !build.value?.can_stop && t('build.stopWait')))
const canOfferStart = computed(() => state.value !== 'building' && state.value !== 'stopped' && props.blockCount > 0 && props.draftCount > 0)
const stage = computed(() => t(`build.stage.${build.value?.stage ?? 'starting'}`))
const spentLine = computed(() => t('build.spent', { spent: usd(build.value?.spent_usd) ?? '$0.00', cap: usd(build.value?.cap_usd) ?? '' }))
const forecastLine = computed(() => (build.value?.forecast ? t('build.forecast', { cost: usd(build.value.forecast.cost_usd) ?? '' }) : t('build.forecastLater')))
const lastEvent = computed(() => build.value?.events[build.value.events.length - 1] ?? null)
const capStop = computed(() => build.value?.stop_code === 'cap')
</script>

<template>
  <section v-if="res.error.value && !res.data.value" class="stack build-panel" data-testid="build-panel" data-state="unknown">
    <p class="danger-text" role="alert">{{ t('build.unknown') }}</p>
    <div><ActionButton action="retry" size="small" data-testid="build-retry" @click="res.reload()" /></div>
  </section>

  <section v-else-if="state === 'building' && build" class="stack build-panel" data-testid="build-panel" data-state="building" aria-live="polite">
    <div class="row">
      <StatusChip tone="info" :label="t('build.building')" />
      <strong data-testid="build-stage">{{ t('build.stageLine', { stage }) }}</strong>
    </div>
    <p data-testid="build-spent">{{ spentLine }}</p>
    <p class="muted" data-testid="build-forecast">{{ forecastLine }}</p>
    <p v-if="lastEvent" class="muted" data-testid="build-event">{{ t('build.latest', { event: lastEvent }) }}</p>
    <p class="hint">{{ t('build.crashNote') }}</p>
    <div class="row">
      <router-link v-if="build.current_run_id" :to="`/runs/${build.current_run_id}`" data-testid="build-run-link">{{ t('build.runLink') }}</router-link>
      <ActionButton action="stop-run" :label="t('build.stop')" size="small" :loading="working" :reason="stopReason" data-testid="build-stop" @click="confirmStop = true" />
    </div>
    <ConfirmDialog v-model:visible="confirmStop" :title="t('build.stopTitle')" :body="t('build.stopBody')" action="stop-run" :label="t('build.stop')" :busy="working" @confirm="stopBuild" />
  </section>

  <section v-else-if="state === 'ready' && build" class="stack build-panel" data-testid="build-panel" data-state="ready">
    <div class="row">
      <StatusChip :tone="build.held_count > 0 ? 'warn' : 'ok'" :label="t('build.ready')" />
      <span data-testid="build-held">{{ build.held_count > 0 ? t('build.readyHeld', { count: build.held_count }) : t('build.readyClean') }}</span>
    </div>
    <div v-if="build.assumed.length > 0" class="stack" data-testid="build-assumed">
      <strong>{{ t('build.assumedTitle') }}</strong>
      <p class="hint">{{ t('build.assumedHelp') }}</p>
      <ul class="plain-list"><li v-for="a in build.assumed" :key="a.key">{{ a.question ?? a.key }}</li></ul>
    </div>
    <div class="row">
      <router-link v-if="build.held_count > 0" :to="`/courses/${courseId}/fix`" data-testid="build-go-fix">{{ t('build.goFix') }}</router-link>
      <router-link :to="`/courses/${courseId}/preview`" data-testid="build-go-preview">{{ t('build.goPreview') }}</router-link>
      <router-link :to="`/courses/${courseId}/publish`" data-testid="build-go-publish">{{ t('build.goPublish') }}</router-link>
    </div>
  </section>

  <section v-else-if="state === 'stopped' && build" class="stack build-panel" data-testid="build-panel" data-state="stopped" :data-stop="build.stop_code ?? 'unknown'">
    <div class="row">
      <StatusChip tone="danger" :label="capStop ? t('build.stoppedCap') : t('build.stopped')" />
    </div>
    <p v-if="capStop" data-testid="build-spent">{{ spentLine }}</p>
    <p v-else-if="build.error" class="mono danger-text" role="alert" data-testid="build-why">{{ t('build.stoppedWhy', { reason: build.error }) }}</p>
    <p class="hint">{{ t('build.stoppedHelp') }}</p>
    <div class="row">
      <router-link v-if="build.stop_code === 'empty_plan'" :to="`/courses/${courseId}/sources`" data-testid="build-go-sources">{{ t('build.goSources') }}</router-link>
      <router-link v-if="build.stop_code === 'tree' || build.stop_code === 'questions'" :to="`/courses/${courseId}/plan`" data-testid="build-go-plan">{{ t('build.goPlan') }}</router-link>
      <router-link v-if="build.current_run_id" :to="`/runs/${build.current_run_id}`" data-testid="build-run-link">{{ t('build.runLink') }}</router-link>
    </div>
    <div class="row build-cap">
      <FormField id="build-cap" :label="t('build.capLabel')" :help="t('build.capHint')" :error="capBad ? t('build.capInvalid') : null">
        <InputText id="build-cap" v-model="capText" inputmode="decimal" data-testid="build-cap" />
      </FormField>
      <ActionButton action="continue-build" :loading="working" :reason="continueReason" data-testid="build-continue" @click="post(true)" />
    </div>
  </section>

  <section v-else-if="canOfferStart" class="stack build-panel" data-testid="build-panel" data-state="idle">
    <p class="hint">{{ t('build.help') }}</p>
    <div class="row build-cap">
      <FormField id="build-cap" :label="t('build.capLabel')" :help="t('build.capHint')" :error="capBad ? t('build.capInvalid') : null">
        <InputText id="build-cap" v-model="capText" inputmode="decimal" data-testid="build-cap" />
      </FormField>
      <ActionButton action="build-course" :label="t('build.startRest')" :loading="working" :reason="startReason" data-testid="build-start" @click="post(false)" />
    </div>
  </section>
</template>
