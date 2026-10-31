import { watchEffect } from 'vue'

export function useDocTitle(title: () => string): void {
  watchEffect(() => { document.title = title() })
}
