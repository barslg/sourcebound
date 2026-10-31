<script setup lang="ts">
const props = defineProps<{ content: Record<string, unknown> }>()
const sentences = Array.isArray(props.content.sentences) ? (props.content.sentences as unknown[]).map(String) : []
</script>

<template>
  <ol data-block="spot_the_error"><li v-for="(s, i) in sentences" :key="i">{{ s }}</li></ol>
</template>
