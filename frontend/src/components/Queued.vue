<script setup lang="ts">
import { Bell } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'
import StatusChip from '@/components/StatusChip.vue'

defineProps<{ compact?: boolean }>()
const { t } = useI18n()
</script>

<template>
  <span class="queued" :class="{ 'queued-compact': compact }" role="status" :data-testid="compact ? 'queued-badge' : 'queued-line'">
    <StatusChip tone="info" :label="t('common.queued')" />
    <template v-if="!compact">
      <span class="muted">{{ t('op.inBell') }}</span>
      <Bell class="queued-bell" :size="16" :stroke-width="2" aria-hidden="true" />
    </template>
  </span>
</template>
