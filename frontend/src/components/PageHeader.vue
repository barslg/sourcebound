<script setup lang="ts">
import Breadcrumbs from '@/components/Breadcrumbs.vue'
import { useRouteMeta, type ObjectRef } from '@/composables/useRouteMeta'

const props = defineProps<{ course?: ObjectRef | null }>()
const meta = useRouteMeta(() => props.course)
</script>

<template>
  <header class="page-head" data-testid="page-head">
    <Breadcrumbs v-if="meta.crumbs.value.length > 0" :crumbs="meta.crumbs.value" />
    <div class="row">
      <h1 class="page-title">{{ meta.title.value }}</h1>
      <div class="row-end"><slot name="actions" /></div>
    </div>
    <p class="muted" data-testid="page-help">{{ meta.purpose.value }}</p>
  </header>
</template>
