<script setup lang="ts">
import Breadcrumbs from '@/components/Breadcrumbs.vue'

defineProps<{ title: string; help: string; crumbs?: Array<{ label: string; to?: string }> }>()
</script>

<template>
  <header class="page-head" data-testid="page-head">
    <Breadcrumbs v-if="crumbs && crumbs.length > 0" :crumbs="crumbs" />
    <div class="row">
      <h1 class="page-title">{{ title }}</h1>
      <div class="row-end"><slot name="actions" /></div>
    </div>
    <p class="muted" data-testid="page-help">{{ help }}</p>
  </header>
</template>
