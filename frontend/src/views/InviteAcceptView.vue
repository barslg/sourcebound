<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { ApiError, request } from '@/api/client'
import { inviteAccept, loginUrl } from '@/api/endpoints'
import type { AcceptResult } from '@/api/types'
import ResourceState from '@/components/ResourceState.vue'
import { useSessionStore } from '@/stores/session'

const { t } = useI18n()
const route = useRoute()
const session = useSessionStore()
const token = String(route.params.token)
const acceptHref = inviteAccept(token)
const signinHref = loginUrl(`/invite/${encodeURIComponent(token)}`)
const loading = ref(false)
const error = ref<ApiError | null>(null)
const scopes = ref<string[] | null>(null)

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
    <h1 class="page-title">{{ t('invite.title') }}</h1>
    <p class="muted" data-testid="page-help">{{ t('invite.help') }}</p>
    <section v-if="!session.signedIn" class="state" data-view="signin">
      <p>{{ t('invite.signinBody') }}</p>
      <a class="p-button p-component" :href="signinHref" data-testid="invite-signin">{{ t('invite.signin') }}</a>
    </section>
    <ResourceState v-else :loading="loading" :error="error" :empty="false" @retry="accept">
      <section class="state" data-view="accepted">
        <h2>{{ t('invite.accepted') }}</h2>
        <p v-if="scopes && scopes.length > 0">{{ t('invite.scopes') }}</p>
        <p v-else>{{ t('invite.noScopes') }}</p>
        <router-link to="/" data-testid="invite-open">{{ t('invite.open') }}</router-link>
      </section>
    </ResourceState>
  </main>
</template>
