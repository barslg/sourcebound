<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import Toast from 'primevue/toast'
import { useToast } from 'primevue/usetoast'
import { bindToast } from '@/composables/notify'
import AppShell from '@/components/AppShell.vue'
import { eventsBus } from '@/api/events'
import { useSessionStore } from '@/stores/session'

bindToast(useToast())
const route = useRoute()
const session = useSessionStore()
const bare = computed(() => route.meta.bare === true || !session.signedIn)

watch(
  () => session.signedIn,
  (inNow) => { if (inNow) eventsBus.start(); else eventsBus.stop() },
  { immediate: true },
)
</script>

<template>
  <Toast position="top-right" />
  <router-view v-if="bare" />
  <AppShell v-else><router-view /></AppShell>
</template>
