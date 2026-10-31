<script setup lang="ts">
import { useI18n } from 'vue-i18n'

defineProps<{ crumbs: Array<{ label: string; to?: string }> }>()
const { t } = useI18n()
</script>

<template>
  <nav :aria-label="t('common.breadcrumb')" data-testid="crumbs">
    <ol class="crumbs">
      <li v-for="(c, i) in crumbs" :key="i">
        <router-link v-if="c.to" :to="c.to">{{ c.label }}</router-link>
        <span v-else aria-current="page">{{ c.label }}</span>
      </li>
    </ol>
  </nav>
</template>
