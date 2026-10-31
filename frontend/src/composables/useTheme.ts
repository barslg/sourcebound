import { ref } from 'vue'

const KEY = 'sourcebound.theme'
const theme = ref<'light' | 'dark'>('light')

function apply(next: 'light' | 'dark'): void {
  theme.value = next
  document.documentElement.dataset.theme = next
}

export function initTheme(): void {
  const stored = localStorage.getItem(KEY)
  const system = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  apply(stored === 'dark' || stored === 'light' ? stored : system)
}

export function useTheme() {
  return {
    theme,
    toggle(): void {
      const next = theme.value === 'dark' ? 'light' : 'dark'
      localStorage.setItem(KEY, next)
      apply(next)
    },
  }
}
