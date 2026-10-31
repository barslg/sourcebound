<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { loginUrl } from '@/api/endpoints'
import ActionButton from '@/components/ActionButton.vue'
import { useDocTitle } from '@/composables/useDocTitle'

const { t } = useI18n()
const href = loginUrl()
useDocTitle(() => `${t('signin.title')} · Sourcebound`)
</script>

<template>
  <main class="centered stack">
    <h1 class="page-title">{{ t('app.name') }}</h1>
    <p class="muted" data-testid="page-help">{{ t('app.tagline') }}</p>
    <section class="state">
      <h2>{{ t('signin.title') }}</h2>
      <p>{{ t('signin.body') }}</p>
      <ActionButton as="a" action="signin" :href="href" :label="t('signin.button')" data-testid="signin" />
    </section>
  </main>
</template>
