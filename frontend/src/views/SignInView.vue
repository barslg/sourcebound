<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { loginUrl } from '@/api/endpoints'
import ActionButton from '@/components/ActionButton.vue'
import { useRouteMeta } from '@/composables/useRouteMeta'

const { t } = useI18n()
const href = loginUrl()
const meta = useRouteMeta()
</script>

<template>
  <main class="centered stack">
    <h1 class="page-title">{{ t('app.name') }}</h1>
    <p class="muted" data-testid="page-help">{{ meta.purpose.value }}</p>
    <section class="state">
      <h2>{{ meta.title.value }}</h2>
      <p>{{ t('signin.body') }}</p>
      <ActionButton as="a" action="signin" :href="href" :label="t('signin.button')" data-testid="signin" />
    </section>
  </main>
</template>
