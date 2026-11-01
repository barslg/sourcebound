<script setup lang="ts">
import DataTable from 'primevue/datatable'

defineOptions({ inheritAttrs: false })
</script>

<template>
  <div class="app-table">
    <DataTable v-bind="$attrs" :pt="{ table: { class: 'app-table-grid' } }">
      <template v-for="name in Object.keys($slots)" #[name]="slotData" :key="name"><slot :name="name" v-bind="slotData ?? {}" /></template>
    </DataTable>
  </div>
</template>
