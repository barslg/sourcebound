<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
</script>

<template>
  <span class="muted"><span aria-hidden="true">&mdash;</span><span class="sr-only">{{ t('common.none') }}</span></span>
</template>
