<script setup lang="ts">
import { useI18n } from 'vue-i18n'
const { t } = useI18n()
</script>

<template>
  <main class="centered state">
    <h1 class="page-title">{{ t('common.emptyPage') }}</h1>
    <router-link to="/">{{ t('invite.open') }}</router-link>
  </main>
</template>
