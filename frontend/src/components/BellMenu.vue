<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from 'primevue/button'
import Popover from 'primevue/popover'
import ProgressBar from 'primevue/progressbar'
import { useOperationsStore } from '@/stores/operations'
import { useSessionStore } from '@/stores/session'
import { ApiError } from '@/api/client'
import { notify } from '@/composables/notify'

const { t } = useI18n()
const ops = useOperationsStore()
const session = useSessionStore()
const panel = ref<InstanceType<typeof Popover> | null>(null)
const busy = ref(false)

function toggle(event: Event): void {
  panel.value?.toggle(event)
  ops.markAllRead()
}

async function runTest(): Promise<void> {
  busy.value = true
  try {
    await ops.submitEcho(t('bell.message'))
  } catch (cause) {
    notify('error', cause instanceof ApiError ? cause.message : String(cause))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Button
    icon="pi pi-bell"
    severity="secondary"
    text
    :aria-label="t('bell.open')"
    data-testid="bell"
    @click="toggle"
  >
    <template #icon>
      <i class="pi pi-bell" aria-hidden="true" />
      <span v-if="ops.unreadCount > 0" class="bell-count" data-testid="bell-count">{{ ops.unreadCount }}</span>
    </template>
  </Button>
  <Popover ref="panel">
    <div class="bell-panel" role="region" :aria-label="t('bell.title')">
      <h2 class="page-title">{{ t('bell.title') }}</h2>
      <p v-if="ops.failure" class="mono danger-text">{{ t('bell.error', { message: ops.failure }) }}</p>
      <p v-if="ops.list.length === 0" class="muted" data-testid="bell-empty">{{ t('bell.empty') }}</p>
      <ul v-else>
        <li v-for="op in ops.list" :key="op.operation_id" class="bell-item" data-testid="bell-item">
          <div class="row">
            <span>{{ t('op.kind', { kind: op.kind }) }}</span>
            <span class="chip row-end">{{ op.status }}</span>
          </div>
          <ProgressBar :value="Math.round(op.progress * 100)" :show-value="false" :aria-label="t('bell.progress', { percent: Math.round(op.progress * 100) })" />
          <p v-if="op.error" class="mono danger-text">{{ t('bell.error', { message: op.error }) }}</p>
        </li>
      </ul>
      <Button
        v-if="session.can('operation.run')"
        :label="busy ? t('bell.running') : t('bell.run')"
        :loading="busy"
        :aria-busy="busy"
        severity="secondary"
        data-testid="bell-run"
        @click="runTest"
      />
    </div>
  </Popover>
</template>
