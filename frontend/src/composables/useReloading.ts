import { onMounted, ref, watch, type Ref, type WatchSource } from 'vue'
import { ApiError } from '@/api/client'

export interface Reloading<T> { data: Ref<T | null>; error: Ref<ApiError | null>; loading: Ref<boolean>; reload: () => Promise<void> }

/** Loads on mount and again whenever `source` changes; a late answer to an older call is dropped. */
export function useReloading<T>(load: () => Promise<T>, source: WatchSource): Reloading<T> {
  const data = ref<T | null>(null) as Ref<T | null>
  const error = ref<ApiError | null>(null)
  const loading = ref(true)
  let ticket = 0
  async function reload(): Promise<void> {
    const mine = ++ticket
    loading.value = data.value === null
    try {
      const next = await load()
      if (mine !== ticket) return
      data.value = next
      error.value = null
    } catch (cause) {
      if (mine === ticket) error.value = cause instanceof ApiError ? cause : new ApiError(0, cause instanceof Error ? cause.message : String(cause))
    } finally {
      if (mine === ticket) loading.value = false
    }
  }
  onMounted(() => { void reload() })
  watch(source, () => { void reload() })
  return { data, error, loading, reload }
}
