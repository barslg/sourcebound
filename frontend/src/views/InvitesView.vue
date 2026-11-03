<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import DatePicker from 'primevue/datepicker'
import InputNumber from 'primevue/inputnumber'
import { ApiError, request } from '@/api/client'
import { ENDPOINTS, inviteRevoke } from '@/api/endpoints'
import type { Invite, InviteListItem } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import FormField from '@/components/FormField.vue'
import ScopePicker from '@/components/ScopePicker.vue'
import StatusChip from '@/components/StatusChip.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import Section from '@/components/Section.vue'
import { coursesForTags, useCourseIndex } from '@/composables/courseIndex'
import { firstReason, lacking } from '@/composables/reasons'
import { notify, notifyError } from '@/composables/notify'
import { dropCached, useResource } from '@/composables/useResource'

interface Made { invite: Invite; id: string | null; revoked: boolean }
const { t, d } = useI18n()
const route = useRoute()
const router = useRouter()
const index = useCourseIndex()
const picked = ref<string[]>(typeof route.query.course === 'string' ? [route.query.course] : [])
const maxUses = ref<number | null>(null)
const expiresOn = ref<Date | null>(null)
const today = new Date()
today.setHours(0, 0, 0, 0)
const expires = computed(() => (expiresOn.value ? expiresOn.value.toLocaleDateString('sv-SE') : ''))
const expiryError = computed(() => (expiresOn.value && expiresOn.value < today ? t('invites.expiresPast') : null))
const busy = ref(false)
const error = ref<string | null>(null)
const created = ref<Made[]>([])
const existing = useResource<InviteListItem[]>('invites', (signal) => request<InviteListItem[]>(ENDPOINTS.invites, { signal }))
const rows = computed(() => (existing.data.value ?? []).map((i) => ({ ...i, opens: coursesForTags(index.courses.value, i.course_tags) })))

function linkOf(token: string): string {
  return new URL(router.resolve(`/invite/${encodeURIComponent(token)}`).href, window.location.origin).toString()
}
const usesText = (i: { max_uses: number | null; used_count: number }): string => (i.max_uses === null ? t('invites.usesUnlimited', { used: i.used_count }) : t('invites.uses', { used: i.used_count, max: i.max_uses }))

async function create(): Promise<void> {
  if (picked.value.length === 0 || expiryError.value) return
  busy.value = true
  error.value = null
  try {
    const before = new Set((await request<InviteListItem[]>(ENDPOINTS.invites)).map((i) => i.id))
    const body: Record<string, unknown> = { course_tags: picked.value }
    if (maxUses.value !== null) body.max_uses = maxUses.value
    if (expires.value) body.expires_at = new Date(`${expires.value}T23:59:59`).toISOString()
    const invite = await request<Invite>(ENDPOINTS.invites, { method: 'POST', body })
    const after = await request<InviteListItem[]>(ENDPOINTS.invites)
    const fresh = after.find((i) => !before.has(i.id)) ?? null
    created.value = [{ invite, id: fresh?.id ?? null, revoked: false }, ...created.value]
    dropCached('invites')
    await existing.reload(true)
    notify('success', t('invites.created'))
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : String(cause)
  } finally {
    busy.value = false
  }
}

const revoking = ref<{ id: string; tags: string[] } | null>(null)
async function revokeId(id: string): Promise<void> {
  try {
    await request(inviteRevoke(id), { method: 'POST' })
    created.value = created.value.map((m) => (m.id === id ? { ...m, revoked: true } : m))
    dropCached('invites')
    await existing.reload(true)
    notify('success', t('invites.revoked'))
  } catch (cause) {
    notifyError(cause, () => void revokeId(id))
  }
}

async function copy(token: string): Promise<void> {
  await navigator.clipboard.writeText(linkOf(token))
  notify('success', t('invites.copied'))
}
</script>

<template>
  <PageHeader />
  <form id="invite-form" class="stack" @submit.prevent="create">
    <ScopePicker id="inv-course" v-model="picked" by="course" />
    <FormField id="inv-max" :label="t('invites.maxUses')" :help="t('invites.maxUsesHint')">
      <InputNumber v-model="maxUses" input-id="inv-max" :min="1" :use-grouping="false" />
    </FormField>
    <FormField id="inv-exp" :label="t('invites.expires')" :help="t('invites.expiresHint')" :error="expiryError">
      <DatePicker v-model="expiresOn" input-id="inv-exp" :min-date="today" show-icon icon-display="input" date-format="yy-mm-dd" :manual-input="false" data-testid="invite-expires" />
    </FormField>
    <p v-if="error" class="mono danger-text" role="alert" data-testid="form-error">{{ error }}</p>
    <div class="row">
      <ActionButton type="submit" action="create" :label="t('invites.create')" :loading="busy" :reason="firstReason(lacking('invite.create'), picked.length === 0 && t('invites.pickCourse'))" data-testid="invite-create" />
    </div>
  </form>
  <section class="stack">
    <Section :title="t('invites.createdNow')" :help="t('invites.linkOnce')" />
    <p v-if="created.length === 0" class="muted" data-testid="invite-none-new">{{ t('invites.noneNew') }}</p>
    <ul v-else class="bell-panel invite-list" data-testid="invite-list">
      <li v-for="m in created" :key="m.invite.token" class="bell-item" data-testid="invite-item">
        <div class="row">
          <span class="mono" data-testid="invite-link">{{ linkOf(m.invite.token) }}</span>
          <StatusChip v-if="m.revoked" tone="warn" :label="t('invites.revokedChip')" />
        </div>
        <div class="row muted">
          <span>{{ usesText(m.invite) }}</span>
          <span>{{ m.invite.expires_at ? t('invites.expiresOn', { date: d(new Date(m.invite.expires_at), 'short') }) : t('invites.noExpiry') }}</span>
          <router-link v-for="c in coursesForTags(index.courses.value, m.invite.course_tags)" :key="c.id" :to="`/courses/${c.id}/publish`">{{ c.title }}</router-link>
        </div>
        <div class="row">
          <ActionButton action="copy-link" size="small" :label="t('invites.copy')" @click="copy(m.invite.token)" />
          <ActionButton action="revoke" size="small" :label="t('invites.revoke')" :reason="firstReason(m.revoked && t('invites.alreadyRevoked'), !m.id && t('invites.reloadToRevoke'))" @click="m.id && (revoking = { id: m.id, tags: m.invite.course_tags })" />
        </div>
      </li>
    </ul>
    <Section :title="t('invites.earlier')" :help="t('invites.earlierHelp')" />
    <ResourceState :loading="existing.loading.value" :error="existing.error.value" :empty="rows.length === 0" :empty-text="t('invites.empty')" @retry="existing.reload()">
      <template #empty-actions><a href="#invite-form">{{ t('invites.emptyAction') }}</a></template>
      <ul class="plain-list" data-testid="invite-existing">
        <li v-for="i in rows" :key="i.id" class="bell-item row" data-testid="invite-existing-item">
          <router-link v-for="c in i.opens" :key="c.id" :to="`/courses/${c.id}/publish`">{{ c.title }}</router-link>
          <span v-if="i.opens.length === 0" class="muted">{{ t('invites.unknownCourse') }}</span>
          <span class="muted">{{ usesText(i) }}</span>
          <span class="muted">{{ i.expires_at ? t('invites.expiresOn', { date: d(new Date(i.expires_at), 'short') }) : t('invites.noExpiry') }}</span>
          <StatusChip v-if="i.revoked_at" tone="warn" :label="t('invites.revokedChip')" />
          <ActionButton v-else class="row-end" action="revoke" size="small" :label="t('invites.revoke')" :reason="lacking('invite.revoke')" @click="revoking = { id: i.id, tags: i.course_tags }" />
        </li>
      </ul>
    </ResourceState>
  </section>
  <ConfirmDialog :visible="revoking !== null" :title="t('invites.revokeTitle', { name: revoking ? coursesForTags(index.courses.value, revoking.tags).map((c) => c.title).join(', ') || t('invites.unknownCourse') : '' })" :body="t('invites.revokeBody')" action="revoke" :label="t('invites.revoke')" @update:visible="(v: boolean) => { if (!v) revoking = null }" @confirm="revoking && revokeId(revoking.id).then(() => { revoking = null })" />
</template>
