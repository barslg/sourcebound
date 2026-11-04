<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import Drawer from 'primevue/drawer'

const props = defineProps<{ visible: boolean; header: string; wide?: boolean }>()
const emit = defineEmits<{ 'update:visible': [value: boolean] }>()
const opener = ref<HTMLElement | null>(null)

function focusFirst(): void {
  const attempt = (): void => {
    const roots = document.querySelectorAll<HTMLElement>('[role="dialog"], .p-drawer')
    const root = roots[roots.length - 1]
    const target = root?.querySelector<HTMLElement>('[autofocus], form input:not([type=hidden]), form textarea, form select, form button:not(:disabled), button:not(:disabled), a[href]')
    if (target && !root?.contains(document.activeElement)) target.focus()
  }
  const watcher = new MutationObserver(() => {
    attempt()
    const roots = document.querySelectorAll<HTMLElement>('[role="dialog"], .p-drawer')
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
  <Drawer :visible="visible" position="right" :header="header" :class="props.wide ? 'drawer-logs' : 'drawer-md'" @update:visible="emit('update:visible', $event)">
    <slot />
  </Drawer>
</template>
