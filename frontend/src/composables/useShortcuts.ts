import { onBeforeUnmount, onMounted, ref } from 'vue'

const typing = (el: EventTarget | null): boolean => {
  const node = el as HTMLElement | null
  return node !== null && (node.matches?.('input, textarea, select, [contenteditable="true"]') ?? false)
}

/** Global keys: "/" focuses the page search, "?" opens the sheet, Ctrl/Cmd+S clicks the page's Save. */
export function useShortcuts() {
  const sheet = ref(false)
  function onKey(event: KeyboardEvent): void {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
      const save = document.querySelector<HTMLButtonElement>('main [data-action="save"]:not(:disabled)')
      if (save) {
        event.preventDefault()
        save.click()
      }
      return
    }
    if (event.ctrlKey || event.metaKey || event.altKey || typing(event.target)) return
    if (document.querySelector('[role="dialog"]')) return
    if (event.key === '/') {
      const search = document.querySelector<HTMLInputElement>('[data-search]')
      if (search) {
        event.preventDefault()
        search.focus()
      }
    } else if (event.key === '?') {
      event.preventDefault()
      sheet.value = true
    }
  }
  onMounted(() => window.addEventListener('keydown', onKey))
  onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
  return { sheet }
}
