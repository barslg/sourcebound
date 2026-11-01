import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const pending = new Map<string, string>()
let scheduled = false

export function useUrlParam(name: string, fallback = '') {
  const route = useRoute()
  const router = useRouter()

  function flush(): void {
    scheduled = false
    const query: Record<string, string> = {}
    for (const [k, v] of Object.entries(route.query)) if (typeof v === 'string') query[k] = v
    for (const [k, v] of pending) {
      if (v === '') Reflect.deleteProperty(query, k)
      else query[k] = v
    }
    pending.clear()
    void router.replace({ query })
  }

  return computed<string>({
    get: () => {
      const raw = route.query[name]
      return typeof raw === 'string' ? raw : fallback
    },
    set: (value) => {
      pending.set(name, value === fallback ? '' : value)
      if (!scheduled) {
        scheduled = true
        queueMicrotask(flush)
      }
    },
  })
}
