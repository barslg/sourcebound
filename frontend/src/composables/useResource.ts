import { onMounted, onScopeDispose, ref, type Ref } from 'vue'
import { ApiError } from '@/api/client'

const cache = new Map<string, { data: unknown; signature: string }>()

export interface Resource<T> {
  data: Ref<T | null>
  error: Ref<ApiError | null>
  loading: Ref<boolean>
  reload: (silent?: boolean) => Promise<void>
}

export function signatureOf(value: unknown): string {
  return JSON.stringify(value)
}

export function useResource<T>(key: string, loader: (signal: AbortSignal) => Promise<T>): Resource<T> {
  const hit = cache.get(key)
  const data = ref<T | null>((hit?.data as T | undefined) ?? null) as Ref<T | null>
  const error = ref<ApiError | null>(null)
  const loading = ref(hit === undefined)
  let signature = hit?.signature ?? ''
  let controller: AbortController | null = null

  async function reload(silent = false): Promise<void> {
    controller?.abort()
    controller = new AbortController()
    const mine = controller
    if (!silent && data.value === null) loading.value = true
    try {
      const next = await loader(mine.signal)
      if (mine.signal.aborted) return
      const sig = signatureOf(next)
      if (sig !== signature) {
        signature = sig
        data.value = next
        cache.set(key, { data: next, signature: sig })
      }
      error.value = null
    } catch (cause) {
      if (mine.signal.aborted) return
      error.value = cause instanceof ApiError ? cause : new ApiError(0, cause instanceof Error ? cause.message : String(cause))
    } finally {
      if (!mine.signal.aborted) loading.value = false
    }
  }

  onMounted(() => { void reload(hit !== undefined) })
  onScopeDispose(() => controller?.abort())
  return { data, error, loading, reload }
}

export function dropCached(prefix: string): void {
  for (const k of cache.keys()) if (k.startsWith(prefix)) cache.delete(k)
}

export function clearAllCached(): void {
  cache.clear()
}
