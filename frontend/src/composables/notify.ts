import type { ToastMessageOptions } from 'primevue/toast'
import type { ToastServiceMethods } from 'primevue/toastservice'

type Severity = 'success' | 'info' | 'warn' | 'error'

let toast: ToastServiceMethods | null = null

export function bindToast(service: ToastServiceMethods): void {
  toast = service
}

/** Success, info and warning fade after 4 s; an error stays until closed and may offer a retry for idempotent actions. */
export function notify(severity: Severity, summary: string, detail?: string, retry?: () => void): void {
  toast?.add({ severity, summary, detail, life: severity === 'error' ? undefined : 4000, closable: true, data: { retry } } as ToastMessageOptions)
}

export function notifyError(cause: unknown, retry?: () => void): void {
  notify('error', cause instanceof Error ? cause.message : String(cause), undefined, retry)
}
