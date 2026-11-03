<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { ApiError } from '@/api/client'
import ActionButton from '@/components/ActionButton.vue'

const props = defineProps<{ loading: boolean; error: ApiError | null; empty: boolean; emptyText?: string; notFoundText?: string }>()
const emit = defineEmits<{ retry: [] }>()
const { t } = useI18n()
const denied = computed(() => props.error !== null && props.error.status === 403)
const notFound = computed(() => props.error !== null && props.error.status === 404 && !!props.notFoundText)
</script>

<template>
  <div v-if="loading" class="state" role="status" aria-live="polite" data-view="loading">
    <span class="sr-only">{{ t('common.loading') }}</span>
    <div class="skeleton-line skeleton-w60" aria-hidden="true" />
    <div class="skeleton-line" aria-hidden="true" />
    <div class="skeleton-line skeleton-w80" aria-hidden="true" />
  </div>
  <div v-else-if="denied" class="state state-denied" role="alert" data-view="denied">
    <h2>{{ t('common.deniedTitle') }}</h2>
    <p>{{ error?.message }}</p>
    <router-link to="/" data-testid="denied-home">{{ t('common.deniedHome') }}</router-link>
  </div>
  <div v-else-if="notFound" class="state" role="status" data-view="not-found">
    <h2>{{ t('common.notFoundTitle') }}</h2>
    <p>{{ notFoundText }}</p>
    <slot name="not-found-actions" />
  </div>
  <div v-else-if="error" class="state state-error" role="alert" data-view="error">
    <h2>{{ t('common.errorTitle') }}</h2>
    <p class="mono">{{ error.message }}</p>
    <ActionButton action="retry" @click="emit('retry')" />
  </div>
  <div v-else-if="empty" class="state" data-view="empty">
    <p class="muted">{{ emptyText ?? t('common.emptyPage') }}</p>
    <slot name="empty-actions" />
  </div>
  <slot v-else />
</template>
