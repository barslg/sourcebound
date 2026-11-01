<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import Dialog from 'primevue/dialog'

const props = defineProps<{ visible: boolean; header: string; size?: 'sm' | 'md' | 'lg'; closable?: boolean }>()
const emit = defineEmits<{ 'update:visible': [value: boolean] }>()
const opener = ref<HTMLElement | null>(null)

function focusFirst(): void {
  const attempt = (): void => {
    const roots = document.querySelectorAll<HTMLElement>('[role="dialog"]')
    const root = roots[roots.length - 1]
    const target = root?.querySelector<HTMLElement>('[autofocus], form input:not([type=hidden]), form textarea, form select, form button:not(:disabled), button:not(:disabled), a[href]')
    if (target && !root?.contains(document.activeElement)) target.focus()
  }
  const watcher = new MutationObserver(() => {
    attempt()
    const roots = document.querySelectorAll<HTMLElement>('[role="dialog"]')
    if (roots[roots.length - 1]?.contains(document.activeElement)) watcher.disconnect()
  })
  watcher.observe(document.body, { childList: true, subtree: true })
  setTimeout(() => watcher.disconnect(), 1500)
  void nextTick(() => requestAnimationFrame(attempt))
}

watch(() => props.visible, (open) => {
  if (open) {
    opener.value = document.activeElement instanceof HTMLElement ? document.activeElement : null
    focusFirst()
  }
  else {
    void nextTick(() => opener.value?.focus())
    setTimeout(() => { if (opener.value && (document.activeElement === document.body || !document.activeElement)) opener.value.focus() }, 250)
  }
}, { flush: 'sync' })
</script>

<template>
  <Dialog :visible="visible" modal :header="header" :closable="closable ?? true" :class="`dlg-${size ?? 'md'}`" @update:visible="emit('update:visible', $event)">
    <slot />
  </Dialog>
</template>
