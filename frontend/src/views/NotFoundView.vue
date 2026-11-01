<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useRouteMeta } from '@/composables/useRouteMeta'
const { t } = useI18n()
const meta = useRouteMeta()
</script>

<template>
  <section class="centered state">
    <h1 class="page-title">{{ meta.title.value }}</h1>
    <p class="muted" data-testid="page-help">{{ meta.purpose.value }}</p>
    <router-link to="/">{{ t('notFound.open') }}</router-link>
  </section>
</template>
