<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { loginUrl } from '@/api/endpoints'

const { t } = useI18n()
const href = loginUrl()
</script>

<template>
  <main class="centered stack">
    <h1 class="page-title">{{ t('app.name') }}</h1>
    <p class="muted" data-testid="page-help">{{ t('app.tagline') }}</p>
    <section class="state">
      <h2>{{ t('signin.title') }}</h2>
      <p>{{ t('signin.body') }}</p>
      <a class="p-button p-component" :href="href" data-testid="signin">{{ t('signin.button') }}</a>
    </section>
  </main>
</template>
