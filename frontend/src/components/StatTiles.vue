<script setup lang="ts">
defineProps<{ tiles: { id: string; label: string; value: string }[] }>()
</script>

<template>
  <ul class="stat-tiles">
    <li v-for="tile in tiles" :key="tile.id" class="stat-tile" :data-tile="tile.id">
      <span class="stat-value">{{ tile.value }}</span>
      <span class="stat-label">{{ tile.label }}</span>
    </li>
  </ul>
</template>
