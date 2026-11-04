<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { request } from '@/api/client'
import { runCall } from '@/api/endpoints'
import type { ApiError } from '@/api/client'
import type { RunCallDetail } from '@/api/types'
import AppDrawer from '@/components/AppDrawer.vue'
import { useCallLabels } from '@/composables/callLabels'

/** What one model call was asked and what it returned, read on demand from the run's call route. */
const props = defineProps<{ visible: boolean; runId: string | null; callId: string | null }>()
const emit = defineEmits<{ 'update:visible': [value: boolean] }>()
const { t } = useI18n()
const { agentName } = useCallLabels()
const shown = ref<RunCallDetail | null>(null)
const failure = ref<ApiError | null>(null)
const loading = ref(false)

watch(() => [props.visible, props.runId, props.callId] as const, async ([visible, runId, callId]) => {
  if (!visible || !runId || !callId) return
  shown.value = null
  failure.value = null
  loading.value = true
  try {
    shown.value = await request<RunCallDetail>(runCall(runId, callId))
  } catch (cause) {
    failure.value = cause as ApiError
  } finally {
    loading.value = false
  }
}, { immediate: true })
const pretty = (value: unknown): string => (value === null || value === undefined ? t('calls.noReply') : JSON.stringify(value, null, 2))
</script>

<template>
  <AppDrawer :visible="visible" wide :header="shown ? t('calls.drawerTitle', { agent: agentName(shown.agent), model: shown.display_name }) : t('calls.inspect')" @update:visible="emit('update:visible', $event)">
    <p v-if="loading" class="muted" role="status" data-testid="call-loading">{{ t('common.loading') }}</p>
    <div v-else-if="failure" class="stack" role="alert" data-testid="call-error">
      <p class="mono">{{ failure.message }}</p>
      <p class="hint">{{ t('calls.unavailable') }}</p>
    </div>
    <div v-else-if="shown" class="stack" data-testid="call-detail">
      <p class="hint">{{ t('calls.drawerHelp', { model: shown.model }) }}</p>
      <h3>{{ t('calls.request') }}</h3>
      <pre class="call-json mono" tabindex="0" data-testid="call-request">{{ pretty(shown.request) }}</pre>
      <h3>{{ t('calls.response') }}</h3>
      <pre class="call-json mono" tabindex="0" data-testid="call-response">{{ pretty(shown.response) }}</pre>
    </div>
  </AppDrawer>
</template>
