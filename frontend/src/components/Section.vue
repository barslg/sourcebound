<script setup lang="ts">
defineProps<{ title: string; help: string; id?: string }>()
</script>

<template>
  <h2 :id="id">{{ title }}</h2>
  <p class="hint" data-testid="section-help">{{ help }}</p>
</template>
