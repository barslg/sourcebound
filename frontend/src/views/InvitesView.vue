<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import { ApiError, request } from '@/api/client'
import { ENDPOINTS, inviteRevoke } from '@/api/endpoints'
import type { Invite, InviteListItem } from '@/api/types'
import GuardedButton from '@/components/GuardedButton.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import SectionHeading from '@/components/SectionHeading.vue'
import { coursesForTags, useCourseIndex } from '@/composables/courseIndex'
import { firstReason, lacking } from '@/composables/reasons'
import { notify } from '@/composables/notify'
import { dropCached, useResource } from '@/composables/useResource'

interface Made { invite: Invite; id: string | null; revoked: boolean }
const { t, d } = useI18n()
const route = useRoute()
const router = useRouter()
const index = useCourseIndex()
const picked = ref<string | null>(typeof route.query.course === 'string' ? route.query.course : null)
const maxUses = ref<number | null>(null)
const expires = ref('')
const busy = ref(false)
const error = ref<string | null>(null)
const created = ref<Made[]>([])
const existing = useResource<InviteListItem[]>('invites', (signal) => request<InviteListItem[]>(ENDPOINTS.invites, { signal }))
const withTags = computed(() => index.courses.value.filter((c) => c.tags.length > 0))
const chosen = computed(() => index.courses.value.find((c) => c.id === picked.value) ?? null)
const rows = computed(() => (existing.data.value ?? []).map((i) => ({ ...i, opens: coursesForTags(index.courses.value, i.course_tags) })))
watch(withTags, (list) => { if (picked.value === null && list.length === 1) picked.value = list[0]?.id ?? null })

function linkOf(token: string): string {
  return new URL(router.resolve(`/invite/${encodeURIComponent(token)}`).href, window.location.origin).toString()
}
const usesText = (i: { max_uses: number | null; used_count: number }): string => (i.max_uses === null ? t('invites.usesUnlimited', { used: i.used_count }) : t('invites.uses', { used: i.used_count, max: i.max_uses }))

async function create(): Promise<void> {
  if (!chosen.value) return
  busy.value = true
  error.value = null
  try {
    const before = new Set((await request<InviteListItem[]>(ENDPOINTS.invites)).map((i) => i.id))
    const body: Record<string, unknown> = { course_tags: chosen.value.tags, granted_role: 'student' }
    if (maxUses.value !== null) body.max_uses = maxUses.value
    if (expires.value.trim()) body.expires_at = new Date(expires.value.trim()).toISOString()
    const invite = await request<Invite>(ENDPOINTS.invites, { method: 'POST', body })
    const after = await request<InviteListItem[]>(ENDPOINTS.invites)
    const fresh = after.find((i) => !before.has(i.id)) ?? null
    created.value = [{ invite, id: fresh?.id ?? null, revoked: false }, ...created.value]
    dropCached('invites')
    await existing.reload(true)
    notify('success', t('invites.created'))
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : cause instanceof RangeError ? cause.message : String(cause)
  } finally {
    busy.value = false
  }
}

async function revokeId(id: string): Promise<void> {
  try {
    await request(inviteRevoke(id), { method: 'POST' })
    created.value = created.value.map((m) => (m.id === id ? { ...m, revoked: true } : m))
    dropCached('invites')
    await existing.reload(true)
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
  <PageHeader :title="t('invites.title')" :help="t('invites.intro')" />
  <form id="invite-form" class="stack" @submit.prevent="create">
    <div class="field">
      <label for="inv-course">{{ t('invites.course') }}</label>
      <Select v-model="picked" input-id="inv-course" :options="withTags" option-label="title" option-value="id" :placeholder="t('invites.coursePlaceholder')" />
      <span class="hint">{{ t('invites.courseHint') }}</span>
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
      <GuardedButton type="submit" :label="t('invites.create')" :loading="busy" :reason="firstReason(lacking('invite.create'), !chosen && t('invites.pickCourse'))" data-testid="invite-create" />
    </div>
  </form>
  <section class="stack">
    <SectionHeading :title="t('invites.createdNow')" :help="t('invites.linkOnce')" />
    <p v-if="created.length === 0" class="muted" data-testid="invite-none-new">{{ t('invites.noneNew') }}</p>
    <ul v-else class="bell-panel invite-list" data-testid="invite-list">
      <li v-for="m in created" :key="m.invite.token" class="bell-item" data-testid="invite-item">
        <div class="row">
          <span class="mono" data-testid="invite-link">{{ linkOf(m.invite.token) }}</span>
          <span v-if="m.revoked" class="chip chip-warn">{{ t('invites.revokedChip') }}</span>
        </div>
        <div class="row muted">
          <span>{{ usesText(m.invite) }}</span>
          <span>{{ m.invite.expires_at ? t('invites.expiresOn', { date: d(new Date(m.invite.expires_at), 'short') }) : t('invites.noExpiry') }}</span>
          <router-link v-for="c in coursesForTags(index.courses.value, m.invite.course_tags)" :key="c.id" :to="`/courses/${c.id}/publish`">{{ c.title }}</router-link>
        </div>
        <div class="row">
          <Button :label="t('invites.copy')" size="small" severity="secondary" @click="copy(m.invite.token)" />
          <GuardedButton :label="t('invites.revoke')" size="small" severity="danger" outlined :reason="firstReason(m.revoked && t('invites.alreadyRevoked'), !m.id && t('invites.reloadToRevoke'))" @click="m.id && revokeId(m.id)" />
        </div>
      </li>
    </ul>
    <SectionHeading :title="t('invites.earlier')" :help="t('invites.earlierHelp')" />
    <ResourceState :loading="existing.loading.value" :error="existing.error.value" :empty="rows.length === 0" :empty-text="t('invites.empty')" @retry="existing.reload()">
      <template #empty-actions><a href="#invite-form">{{ t('invites.emptyAction') }}</a></template>
      <ul class="plain-list" data-testid="invite-existing">
        <li v-for="i in rows" :key="i.id" class="bell-item row" data-testid="invite-existing-item">
          <router-link v-for="c in i.opens" :key="c.id" :to="`/courses/${c.id}/publish`">{{ c.title }}</router-link>
          <span v-if="i.opens.length === 0" class="muted">{{ t('invites.unknownCourse') }}</span>
          <span class="muted">{{ usesText(i) }}</span>
          <span class="muted">{{ i.expires_at ? t('invites.expiresOn', { date: d(new Date(i.expires_at), 'short') }) : t('invites.noExpiry') }}</span>
          <span v-if="i.revoked_at" class="chip chip-warn">{{ t('invites.revokedChip') }}</span>
          <GuardedButton v-else class="row-end" :label="t('invites.revoke')" size="small" severity="danger" outlined :reason="lacking('invite.revoke')" @click="revokeId(i.id)" />
        </li>
      </ul>
    </ResourceState>
  </section>
</template>
