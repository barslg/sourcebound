<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError, request } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import type { ModelKeyStatus } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import FormField from '@/components/FormField.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import Section from '@/components/Section.vue'
import { notify, notifyError } from '@/composables/notify'
import { useReloading } from '@/composables/useReloading'
import { useSessionStore } from '@/stores/session'

/** The signed-in person's own page: their Meta API key, stored encrypted and never shown back. */
const { t, d } = useI18n()
const session = useSessionStore()
const status = useReloading<ModelKeyStatus>(() => request<ModelKeyStatus>(ENDPOINTS.modelKey), () => [])
const draft = ref('')
const busy = ref(false)
const error = ref<string | null>(null)
const confirmRemove = ref(false)
const isSet = computed(() => status.data.value?.is_set === true)
const reason = computed(() => (draft.value.trim().length < 16 ? t('account.keyTooShort') : null))

async function save(): Promise<void> {
  if (reason.value) return
  busy.value = true
  error.value = null
  try {
    status.data.value = await request<ModelKeyStatus>(ENDPOINTS.modelKey, { method: 'PUT', body: { key: draft.value.trim() } })
    draft.value = ''
    notify('success', t('account.keySaved'))
    await session.load()
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : String(cause)
  } finally {
    busy.value = false
  }
}

async function remove(): Promise<void> {
  busy.value = true
  try {
    status.data.value = await request<ModelKeyStatus>(ENDPOINTS.modelKey, { method: 'DELETE' })
    confirmRemove.value = false
    notify('success', t('account.keyRemoved'))
    await session.load()
  } catch (cause) {
    notifyError(cause, () => void remove())
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <PageHeader />
  <ResourceState :loading="status.loading.value" :error="status.error.value" :empty="false" @retry="status.reload()">
    <div v-if="status.data.value" class="stack account-page">
      <p class="muted" data-testid="account-identity">{{ session.me?.display_name || session.me?.email }} · {{ session.me?.email }}</p>
      <Section id="model-key" :title="t('account.keyTitle')" :help="t('account.keyWhy')" />
      <p v-if="isSet" class="row" data-testid="key-status">
        <span>{{ t('account.keySet', { hint: status.data.value.hint }) }}</span>
        <span v-if="status.data.value.updated_at" class="muted">{{ t('account.keyUpdated', { date: d(new Date(status.data.value.updated_at), 'short') }) }}</span>
      </p>
      <p v-else class="muted" data-testid="key-status">{{ t('account.keyNotSet') }} <span v-if="session.me?.model_key_ready">{{ t('account.keyFallback') }}</span></p>
      <Section id="key-leads" :title="t('account.leadsTitle')" :help="t('account.leadsHelp')" />
      <ul class="plain-list" data-testid="key-leads">
        <li>{{ t('account.leadsBilled') }}</li>
        <li>{{ t('account.leadsTutor') }}</li>
        <li><a href="https://dev.meta.ai" target="_blank" rel="noopener">{{ t('account.leadsGet') }}</a></li>
        <li><router-link to="/courses">{{ t('account.leadsCourses') }}</router-link></li>
      </ul>
      <form class="stack" @submit.prevent="save">
        <FormField id="model-key-input" :label="isSet ? t('account.keyReplace') : t('account.keyLabel')" :help="t('account.keyHelp')" :error="error">
          <input id="model-key-input" v-model="draft" type="password" autocomplete="off" spellcheck="false" class="p-inputtext key-input" data-testid="key-input" />
        </FormField>
        <div class="row">
          <ActionButton type="submit" action="save" :label="t('account.keySave')" :loading="busy" :reason="reason" data-testid="key-save" />
          <ActionButton v-if="isSet" action="delete" :label="t('account.keyRemove')" :reason="busy ? t('account.busy') : null" data-testid="key-remove" @click="confirmRemove = true" />
        </div>
      </form>
      <p class="hint">{{ t('account.keyPrivacy') }}</p>
    </div>
  </ResourceState>
  <ConfirmDialog v-model:visible="confirmRemove" :title="t('account.keyRemoveTitle')" :body="t('account.keyRemoveBody')" action="delete" :label="t('account.keyRemove')" :busy="busy" @confirm="remove" />
</template>
