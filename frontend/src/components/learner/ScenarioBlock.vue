<script setup lang="ts">
const props = defineProps<{ content: Record<string, unknown> }>()
const items = Array.isArray(props.content.items) ? (props.content.items as Array<Record<string, unknown>>) : []
</script>

<template>
  <div class="stack" data-block="scenario_practice">
    <p>{{ String(content.scenario ?? '') }}</p>
    <ul><li v-for="(it, i) in items" :key="i">{{ String(it.text ?? '') }}</li></ul>
  </div>
</template>
