<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import Toast from 'primevue/toast'
import { useToast } from 'primevue/usetoast'
import { bindToast } from '@/composables/notify'
import ActionButton from '@/components/ActionButton.vue'
import AppShell from '@/components/AppShell.vue'
import { eventsBus } from '@/api/events'
import { useSessionStore } from '@/stores/session'

const toast = useToast()
bindToast(toast)
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
  <Toast position="bottom-right" :pt="{ root: { 'data-testid': 'toast-region' } }">
    <template #message="{ message }">
      <div class="toast-body">
        <strong>{{ message.summary }}</strong>
        <span v-if="message.detail">{{ message.detail }}</span>
        <ActionButton v-if="message.data?.retry" action="retry" size="small" @click="() => { toast.remove(message); message.data.retry() }" />
      </div>
    </template>
  </Toast>
  <router-view v-if="bare" />
  <AppShell v-else><router-view /></AppShell>
</template>
