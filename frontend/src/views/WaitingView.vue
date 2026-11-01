<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import ActionButton from '@/components/ActionButton.vue'
import { useRouteMeta } from '@/composables/useRouteMeta'
import { useSessionStore } from '@/stores/session'

const { t } = useI18n()
const session = useSessionStore()
const router = useRouter()
const checking = ref(false)
const still = ref(false)
const meta = useRouteMeta()

async function check(): Promise<void> {
  checking.value = true
  still.value = false
  await session.load()
  checking.value = false
  if (!session.signedIn) await router.push('/signin')
  else if (session.isSuperadmin || session.permissions.length > 0) await router.push('/')
  else still.value = true
}
async function signOut(): Promise<void> {
  await session.logout()
  await router.push('/signin')
}
</script>

<template>
  <main class="centered stack">
    <section class="state">
      <h1 class="page-title">{{ meta.title.value }}</h1>
      <p data-testid="page-help">{{ meta.purpose.value }}</p>
      <p v-if="session.me" class="mono">{{ session.me.email }}</p>
      <p v-if="still" role="status" class="muted">{{ t('waiting.stillWaiting') }}</p>
      <div class="row">
        <ActionButton action="check-again" :label="t('waiting.check')" :loading="checking" data-testid="waiting-check" @click="check" />
        <ActionButton action="signout" :label="t('shell.signOut')" @click="signOut" />
      </div>
    </section>
  </main>
</template>
