import type { ToastServiceMethods } from 'primevue/toastservice'

type Severity = 'success' | 'info' | 'warn' | 'error'

let toast: ToastServiceMethods | null = null

export function bindToast(service: ToastServiceMethods): void {
  toast = service
}

export function notify(severity: Severity, summary: string, detail?: string): void {
  toast?.add({ severity, summary, detail, life: severity === 'error' ? 8000 : 4000 })
}
