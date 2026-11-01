import { createApp } from 'vue'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import ToastService from 'primevue/toastservice'
import Tooltip from 'primevue/tooltip'
import App from './App.vue'
import { configureClient } from './api/client'
import { eventsBus } from './api/events'
import { i18n } from './i18n'
import { router } from './router'
import { useOperationsStore } from './stores/operations'
import { useSessionStore } from './stores/session'
import './theme/tokens.css'
import { preset } from './theme/preset'
import { initTheme } from './composables/useTheme'

initTheme()

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
