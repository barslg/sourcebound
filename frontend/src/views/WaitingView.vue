<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import { useSessionStore } from '@/stores/session'

const { t } = useI18n()
const session = useSessionStore()
const router = useRouter()
const checking = ref(false)
const still = ref(false)

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
      <h1 class="page-title">{{ t('waiting.title') }}</h1>
      <p>{{ t('waiting.body') }}</p>
      <p v-if="session.me" class="mono">{{ session.me.email }}</p>
      <p v-if="still" role="status" class="muted">{{ t('waiting.stillWaiting') }}</p>
      <div class="row">
        <Button :label="t('waiting.check')" :loading="checking" :aria-busy="checking" data-testid="waiting-check" @click="check" />
        <Button :label="t('shell.signOut')" severity="secondary" text @click="signOut" />
      </div>
    </section>
  </main>
</template>
