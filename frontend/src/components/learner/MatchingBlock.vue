<script setup lang="ts">
const props = defineProps<{ content: Record<string, unknown> }>()
const pairs = Array.isArray(props.content.pairs) ? (props.content.pairs as Array<Record<string, unknown>>) : []
</script>

<template>
  <table class="app-table-grid" data-block="matching">
    <tbody><tr v-for="(p, i) in pairs" :key="i"><td>{{ String(p.left ?? '') }}</td><td>{{ String(p.right ?? '') }}</td></tr></tbody>
  </table>
</template>
