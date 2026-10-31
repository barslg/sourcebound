<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from 'primevue/button'
import ProgressSpinner from 'primevue/progressspinner'
import type { ApiError } from '@/api/client'

const props = defineProps<{ loading: boolean; error: ApiError | null; empty: boolean; emptyText?: string }>()
const emit = defineEmits<{ retry: [] }>()
const { t } = useI18n()
const denied = computed(() => props.error !== null && props.error.status === 403)
</script>

<template>
  <div v-if="loading" class="state" role="status" aria-live="polite" data-view="loading">
    <ProgressSpinner stroke-width="4" aria-hidden="true" />
    <p class="muted">{{ t('common.loading') }}</p>
  </div>
  <div v-else-if="denied" class="state state-denied" role="alert" data-view="denied">
    <h2>{{ t('common.deniedTitle') }}</h2>
    <p>{{ error?.message }}</p>
    <router-link to="/" data-testid="denied-home">{{ t('common.deniedHome') }}</router-link>
  </div>
  <div v-else-if="error" class="state state-error" role="alert" data-view="error">
    <h2>{{ t('common.errorTitle') }}</h2>
    <p class="mono">{{ error.message }}</p>
    <Button :label="t('common.retry')" severity="secondary" @click="emit('retry')" />
  </div>
  <div v-else-if="empty" class="state" data-view="empty">
    <p class="muted">{{ emptyText ?? t('common.emptyPage') }}</p>
    <slot name="empty-actions" />
  </div>
  <slot v-else />
</template>
