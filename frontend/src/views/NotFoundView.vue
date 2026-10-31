<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useDocTitle } from '@/composables/useDocTitle'
const { t } = useI18n()
useDocTitle(() => `${t('notFound.title')} · Sourcebound`)
</script>

<template>
  <section class="centered state">
    <h1 class="page-title">{{ t('notFound.title') }}</h1>
    <p class="muted" data-testid="page-help">{{ t('notFound.help') }}</p>
    <router-link to="/">{{ t('notFound.open') }}</router-link>
  </section>
</template>
