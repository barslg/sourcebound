<script setup lang="ts">
const props = defineProps<{ content: Record<string, unknown> }>()
const options = Array.isArray(props.content.options) ? (props.content.options as unknown[]).map(String) : []
</script>

<template>
  <div class="stack" data-block="choice">
    <p>{{ String(content.stem ?? '') }}</p>
    <ol type="A" data-testid="learner-options"><li v-for="(o, i) in options" :key="i">{{ o }}</li></ol>
  </div>
</template>
