<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { firstAllowed } from '@/router/guard'
import { sectionEntries } from '@/router/registry'
import { ApiError, request } from '@/api/client'
import { inviteAccept, loginUrl } from '@/api/endpoints'
import type { AcceptResult } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import { useRouteMeta } from '@/composables/useRouteMeta'
import ResourceState from '@/components/ResourceState.vue'
import { useSessionStore } from '@/stores/session'

const { t } = useI18n()
const route = useRoute()
const session = useSessionStore()
const token = String(route.params.token)
const meta = useRouteMeta()
const acceptHref = inviteAccept(token)
const signinHref = loginUrl(`/invite/${encodeURIComponent(token)}`)
const loading = ref(false)
const error = ref<ApiError | null>(null)
const scopes = ref<string[] | null>(null)
const landing = computed(() => firstAllowed({ signedIn: session.signedIn, isSuperadmin: session.isSuperadmin, permissions: session.permissions }, sectionEntries()))

async function accept(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const res = await request<AcceptResult>(acceptHref, { method: 'POST' })
    scopes.value = res.scopes
    await session.load()
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause : new ApiError(0, String(cause))
  } finally {
    loading.value = false
  }
}
onMounted(() => { if (session.signedIn) void accept() })
</script>

<template>
  <main class="centered stack">
    <h1 class="page-title">{{ meta.title.value }}</h1>
    <p class="muted" data-testid="page-help">{{ meta.purpose.value }}</p>
    <section v-if="!session.signedIn" class="state" data-view="signin">
      <p>{{ t('invite.signinBody') }}</p>
      <ActionButton as="a" action="signin" :href="signinHref" :label="t('invite.signin')" data-testid="invite-signin" />
    </section>
    <ResourceState v-else :loading="loading || (scopes === null && error === null)" :error="error" :empty="false" @retry="accept">
      <section class="state" data-view="accepted">
        <h2>{{ t('invite.accepted') }}</h2>
        <p v-if="scopes && scopes.length > 0">{{ t('invite.scopes') }}</p>
        <p v-else>{{ t('invite.noScopes') }}</p>
        <router-link v-if="landing" :to="landing" data-testid="invite-open">{{ t('invite.open') }}</router-link>
        <p v-else class="muted" data-testid="invite-no-landing">{{ t('invite.noLanding') }}</p>
      </section>
    </ResourceState>
  </main>
</template>
