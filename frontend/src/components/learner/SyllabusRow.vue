<script setup lang="ts">
import type { Component } from 'vue'
import { useI18n } from 'vue-i18n'

defineProps<{ to: string; title: string; moduleTest: boolean; icon: Component; iconClass?: string }>()
const { t } = useI18n()
</script>

<template>
  <li class="syllabus-row" data-testid="topic-row">
    <component :is="icon" :size="20" :stroke-width="2" aria-hidden="true" :class="iconClass" />
    <span class="syllabus-title">
      <router-link :to="to" data-testid="topic-open"><strong>{{ title }}</strong></router-link>
      <span v-if="moduleTest" class="muted"> · {{ t('learnCourse.moduleTest') }}</span>
    </span>
    <slot name="status"><span></span></slot>
    <slot name="end" />
  </li>
</template>
