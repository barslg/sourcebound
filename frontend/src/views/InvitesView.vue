<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import { ApiError, request } from '@/api/client'
import { ENDPOINTS, inviteRevoke } from '@/api/endpoints'
import type { Invite, InviteListItem } from '@/api/types'
import ResourceState from '@/components/ResourceState.vue'
import { notify } from '@/composables/notify'
import { parseTags } from '@/composables/tags'
import { dropCached, useResource } from '@/composables/useResource'
import { useSessionStore } from '@/stores/session'

const props = defineProps<{ embedded?: boolean; presetTags?: string[] }>()
const { t, d } = useI18n()
const router = useRouter()
const session = useSessionStore()
const tagsText = ref((props.presetTags ?? []).join(', '))
const maxUses = ref<number | null>(null)
const expires = ref('')
const busy = ref(false)
const error = ref<string | null>(null)
const created = ref<Invite[]>([])
const existing = useResource<InviteListItem[]>('invites', (signal) => request<InviteListItem[]>(ENDPOINTS.invites, { signal }))
const visible = computed(() => (existing.data.value ?? []).filter((i) => (props.presetTags ?? []).length === 0 || i.course_tags.some((tag) => (props.presetTags ?? []).includes(tag))))

function linkOf(token: string): string {
  return new URL(router.resolve(`/invite/${encodeURIComponent(token)}`).href, window.location.origin).toString()
}

async function create(): Promise<void> {
  busy.value = true
  error.value = null
  try {
    const body: Record<string, unknown> = { course_tags: parseTags(tagsText.value), granted_role: 'student' }
    if (maxUses.value !== null) body.max_uses = maxUses.value
    if (expires.value.trim()) body.expires_at = new Date(expires.value.trim()).toISOString()
    const invite = await request<Invite>(ENDPOINTS.invites, { method: 'POST', body })
    created.value = [invite, ...created.value]
    dropCached('invites')
    void existing.reload(true)
    notify('success', t('invites.created'))
    tagsText.value = (props.presetTags ?? []).join(', ')
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : cause instanceof RangeError ? cause.message : String(cause)
  } finally {
    busy.value = false
  }
}

async function revoke(invite: Invite): Promise<void> {
  try {
    await request(inviteRevoke(invite.token), { method: 'POST' })
    created.value = created.value.map((i) => (i.token === invite.token ? { ...i, revoked_at: new Date().toISOString() } : i))
    dropCached('invites')
    void existing.reload(true)
    notify('success', t('invites.revoked'))
  } catch (cause) {
    notify('error', cause instanceof ApiError ? cause.message : String(cause))
  }
}

async function copy(token: string): Promise<void> {
  await navigator.clipboard.writeText(linkOf(token))
  notify('success', t('invites.copied'))
}
</script>

<template>
  <h2 v-if="embedded">{{ t('invites.title') }}</h2>
  <h1 v-else class="page-title">{{ t('invites.title') }}</h1>
  <p class="muted">{{ t('invites.intro') }}</p>
  <form class="stack" @submit.prevent="create">
    <div class="field">
      <label for="inv-tags">{{ t('invites.tags') }}</label>
      <InputText id="inv-tags" v-model="tagsText" />
      <span class="hint">{{ t('common.tagsHint') }}</span>
    </div>
    <div class="field">
      <label for="inv-max">{{ t('invites.maxUses') }}</label>
      <InputNumber v-model="maxUses" input-id="inv-max" :min="1" :use-grouping="false" />
      <span class="hint">{{ t('invites.maxUsesHint') }}</span>
    </div>
    <div class="field">
      <label for="inv-exp">{{ t('invites.expires') }}</label>
      <InputText id="inv-exp" v-model="expires" />
      <span class="hint">{{ t('invites.expiresHint') }}</span>
    </div>
    <p v-if="error" class="mono danger-text" role="alert" data-testid="form-error">{{ error }}</p>
    <div class="row">
      <Button
        type="submit"
        :label="t('invites.create')"
        :loading="busy"
        :aria-busy="busy"
        :disabled="!session.can('invite.create') || parseTags(tagsText).length === 0"
        data-testid="invite-create"
      />
    </div>
  </form>
  <section class="stack">
    <h2>{{ t('invites.createdNow') }}</h2>
    <p v-if="created.length === 0" class="muted" data-testid="invite-none-new">{{ t('invites.linkOnce') }}</p>
    <ul v-else class="bell-panel invite-list" data-testid="invite-list">
      <li v-for="invite in created" :key="invite.token" class="bell-item" data-testid="invite-item">
        <div class="row">
          <span class="mono" data-testid="invite-link">{{ linkOf(invite.token) }}</span>
          <span v-if="invite.revoked_at" class="chip chip-warn">{{ t('invites.revokedChip') }}</span>
        </div>
        <div class="row muted">
          <span>{{ invite.max_uses === null ? t('invites.usesUnlimited', { used: invite.used_count }) : t('invites.uses', { used: invite.used_count, max: invite.max_uses }) }}</span>
          <span>{{ invite.expires_at ? t('invites.expiresOn', { date: d(new Date(invite.expires_at), 'short') }) : t('invites.noExpiry') }}</span>
          <span v-for="tag in invite.course_tags" :key="tag" class="chip mono">{{ tag }}</span>
        </div>
        <div class="row">
          <Button :label="t('invites.copy')" size="small" severity="secondary" @click="copy(invite.token)" />
          <Button :label="t('invites.revoke')" size="small" severity="danger" outlined :disabled="Boolean(invite.revoked_at)" @click="revoke(invite)" />
        </div>
      </li>
    </ul>
    <h2>{{ t('invites.earlier') }}</h2>
    <ResourceState :loading="existing.loading.value" :error="existing.error.value" :empty="visible.length === 0" :empty-text="t('invites.empty')" @retry="existing.reload()">
      <ul class="plain-list" data-testid="invite-existing">
        <li v-for="invite in visible" :key="invite.id" class="bell-item row muted" data-testid="invite-existing-item">
          <span>{{ invite.max_uses === null ? t('invites.usesUnlimited', { used: invite.used_count }) : t('invites.uses', { used: invite.used_count, max: invite.max_uses }) }}</span>
          <span v-for="tag in invite.course_tags" :key="tag" class="chip mono">{{ tag }}</span>
          <span v-if="invite.revoked_at" class="chip chip-warn">{{ t('invites.revokedChip') }}</span>
        </li>
      </ul>
    </ResourceState>
  </section>
</template>
