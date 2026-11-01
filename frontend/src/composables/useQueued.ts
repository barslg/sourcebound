import { computed, onScopeDispose, ref, watch } from 'vue'
import { isTerminal, useOperationsStore } from '@/stores/operations'

/** One queued state per (kind, course): read from the operations store, so leaving the page and coming back keeps the action disabled until the operation is terminal. */
export function useQueued(tag: () => string, onDone: () => void | Promise<void>) {
  const ops = useOperationsStore()
  const id = ref<string | null>(ops.openFor(tag()))
  let timer: ReturnType<typeof setInterval> | null = null
  const status = computed(() => (id.value ? ops.items[id.value]?.status ?? 'queued' : null))
  const queued = computed(() => id.value !== null)
  function stop(): void { if (timer) { clearInterval(timer); timer = null } }
  function poll(): void {
    stop()
    timer = setInterval(() => { if (id.value) void ops.refresh(id.value) }, 2000)
  }
  function track(operationId: string): void {
    id.value = operationId
    ops.tagOperation(operationId, tag())
    poll()
  }
  if (id.value) { poll(); void ops.refresh(id.value) }
  watch(status, (now) => {
    if (id.value && now && isTerminal(now)) { stop(); id.value = null; void onDone() }
  })
  onScopeDispose(stop)
  return { queued, track }
}
