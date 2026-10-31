<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from 'primevue/button'
import Popover from 'primevue/popover'
import ProgressBar from 'primevue/progressbar'
import { useOperationsStore } from '@/stores/operations'
import { useSessionStore } from '@/stores/session'
import { notificationLabel } from '@/composables/notifications'
import { humanize } from '@/composables/labels'

const { t, te } = useI18n()
const ops = useOperationsStore()
const session = useSessionStore()
const panel = ref<InstanceType<typeof Popover> | null>(null)

onMounted(() => { if (session.signedIn) void ops.loadNotifications() })
watch(() => session.signedIn, (yes) => { if (yes) void ops.loadNotifications() })

function toggle(event: Event): void {
  panel.value?.toggle(event)
  ops.markAllRead()
}

const kindLabel = (kind: string): string => (te(`op.kinds.${kind}`) ? t(`op.kinds.${kind}`) : humanize(kind))
const statusLabel = (status: string): string => (te(`op.statuses.${status}`) ? t(`op.statuses.${status}`) : humanize(status))
const runOf = (result: Record<string, unknown> | null): string | null => (result && typeof result.run_id === 'string' ? result.run_id : null)
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
      <p class="hint" data-testid="section-help">{{ t('bell.help') }}</p>
      <p v-if="ops.failure" class="mono danger-text">{{ t('bell.error', { message: ops.failure }) }}</p>
      <p v-if="ops.list.length === 0 && ops.notifications.length === 0" class="muted" data-testid="bell-empty">{{ t('bell.empty') }}</p>
      <ul v-if="ops.list.length > 0">
        <li v-for="op in ops.list" :key="op.operation_id" class="bell-item" data-testid="bell-item">
          <div class="row">
            <span>{{ kindLabel(op.kind) }}</span>
            <span class="chip row-end">{{ statusLabel(op.status) }}</span>
          </div>
          <ProgressBar :value="Math.round(op.progress * 100)" :show-value="false" :aria-label="t('bell.progress', { percent: Math.round(op.progress * 100) })" />
          <p v-if="op.error" class="mono danger-text">{{ t('bell.error', { message: op.error }) }}</p>
          <router-link v-if="runOf(op.result)" :to="`/runs/${runOf(op.result)}`">{{ t('plan.openRun') }}</router-link>
        </li>
      </ul>
      <template v-if="ops.notifications.length > 0">
        <h3>{{ t('bell.earlier') }}</h3>
        <ul class="plain-list" data-testid="bell-notifications">
          <li v-for="n in ops.notifications" :key="n.id" class="bell-item row" data-testid="bell-notification">
            <span>{{ t(notificationLabel(n.kind)) }}</span>
            <span v-if="!n.read" class="chip row-end">{{ t('bell.unread', { count: 1 }) }}</span>
          </li>
        </ul>
      </template>
    </div>
  </Popover>
</template>
