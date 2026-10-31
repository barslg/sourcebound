import { computed, onScopeDispose, ref, watch } from 'vue'
import { isTerminal, useOperationsStore } from '@/stores/operations'

export function useQueued(onDone: () => void | Promise<void>) {
  const ops = useOperationsStore()
  const id = ref<string | null>(null)
  let timer: ReturnType<typeof setInterval> | null = null
  const status = computed(() => (id.value ? ops.items[id.value]?.status ?? 'queued' : null))
  const queued = computed(() => id.value !== null)
  function stop(): void { if (timer) { clearInterval(timer); timer = null } }
  function track(operationId: string): void {
    id.value = operationId
    stop()
    timer = setInterval(() => { if (id.value) void ops.refresh(id.value) }, 2000)
  }
  watch(status, (now) => {
    if (id.value && now && isTerminal(now)) { stop(); id.value = null; void onDone() }
  })
  onScopeDispose(stop)
  return { queued, track }
}
