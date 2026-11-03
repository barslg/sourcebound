<script setup lang="ts">
import ReferencePage from '@/components/learner/ReferencePage.vue'
</script>

<template>
  <ReferencePage kind="glossary" scope="learn" :back-path="(id) => `/learn/courses/${id}`" :topic-path="(id) => `/learn/topics/${id}`" />
</template>
