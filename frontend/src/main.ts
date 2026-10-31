import { createApp } from 'vue'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ToastService from 'primevue/toastservice'
import Tooltip from 'primevue/tooltip'
import Aura from '@primeuix/themes/aura'
import { definePreset } from '@primeuix/themes'
import App from './App.vue'
import { configureClient } from './api/client'
import { eventsBus } from './api/events'
import { i18n } from './i18n'
import { router } from './router'
import { useOperationsStore } from './stores/operations'
import { useSessionStore } from './stores/session'
import './theme/tokens.css'
import { initTheme } from './composables/useTheme'

initTheme()

const preset = definePreset(Aura, {
  semantic: {
    primary: {
      50: '{indigo.50}', 100: '{indigo.100}', 200: '{indigo.200}', 300: '{indigo.300}', 400: '{indigo.400}',
      500: '{indigo.500}', 600: '{indigo.600}', 700: '{indigo.700}', 800: '{indigo.800}', 900: '{indigo.900}', 950: '{indigo.950}',
    },
    colorScheme: {
      light: { primary: { color: '{indigo.800}', contrastColor: '#ffffff', hoverColor: '{indigo.900}', activeColor: '{indigo.950}' } },
      dark: { primary: { color: '{indigo.300}', contrastColor: '#0b0b12', hoverColor: '{indigo.200}', activeColor: '{indigo.100}' } },
    },
  },
})

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(i18n)
app.use(PrimeVue, { theme: { preset, options: { darkModeSelector: '[data-theme="dark"]', cssLayer: false } } })
app.use(ToastService)
app.directive('tooltip', Tooltip)

const session = useSessionStore()
const operations = useOperationsStore()
configureClient({
  onUnauthorized: () => {
    session.clear()
    operations.reset()
    eventsBus.stop()
    if (router.currentRoute.value.path !== '/signin') void router.push('/signin')
  },
  onAccepted: (id) => operations.register(id),
})
eventsBus.subscribe((m) => operations.onBus(m))
eventsBus.onReconnect(() => operations.refetchOpen())
eventsBus.onSessionEnded(() => {
  session.clear()
  operations.reset()
  void router.push('/signin')
})

app.use(router)
app.mount('#app')
