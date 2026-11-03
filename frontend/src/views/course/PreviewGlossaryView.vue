<script setup lang="ts">
import ReferencePage from '@/components/learner/ReferencePage.vue'
</script>

<template>
  <ReferencePage kind="glossary" scope="preview" :back-path="(id) => `/courses/${id}/preview`" :topic-path="(id) => `/preview/topics/${id}`" />
</template>
