<template>
  <p data-block="cloze">{{ String(content.text ?? '').replaceAll('___', '______') }}</p>
</template>

<script setup lang="ts">
defineProps<{ content: Record<string, unknown> }>()
</script>
