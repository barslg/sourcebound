import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { ApiError, request, resetCsrf } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import type { Me } from '@/api/types'
import { clearAllCached } from '@/composables/useResource'

export const useSessionStore = defineStore('session', () => {
  const me = ref<Me | null>(null)
  const ready = ref(false)
  const failure = ref<string | null>(null)

  const signedIn = computed(() => me.value !== null)
  const permissions = computed(() => me.value?.permissions ?? [])
  const isSuperadmin = computed(() => me.value?.is_superadmin ?? false)

  function can(code: string): boolean {
    return isSuperadmin.value || permissions.value.includes(code)
  }

  async function load(): Promise<void> {
    try {
      me.value = await request<Me>(ENDPOINTS.me, { silent401: true })
      failure.value = null
    } catch (cause) {
      me.value = null
      if (!(cause instanceof ApiError && cause.status === 401)) {
        failure.value = cause instanceof Error ? cause.message : String(cause)
      }
    } finally {
      ready.value = true
    }
  }

  function clear(): void {
    me.value = null
    resetCsrf()
    clearAllCached()
  }

  async function logout(): Promise<void> {
    try {
      await request(ENDPOINTS.logout, { method: 'POST' })
    } finally {
      clear()
    }
  }

  return { me, ready, failure, signedIn, permissions, isSuperadmin, can, load, clear, logout }
})
