<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import Select from 'primevue/select'
import BellMenu from '@/components/BellMenu.vue'
import { LOCALES, setLocale, type AppLocale } from '@/i18n'
import { routes } from '@/router'
import { useSessionStore } from '@/stores/session'
import { useTheme } from '@/composables/useTheme'

const { t, locale } = useI18n()
const session = useSessionStore()
const router = useRouter()
const { theme, toggle } = useTheme()

const nav = computed(() =>
  routes
    .filter((r) => r.meta?.navKey && r.meta.perm && session.can(r.meta.perm))
    .map((r) => ({ path: r.path, label: t(r.meta?.navKey as string) })),
)
const localeOptions = computed(() => LOCALES.map((code) => ({ code, label: t(`locale.${code}`) })))
const current = computed({
  get: () => (LOCALES as readonly string[]).includes(locale.value) ? (locale.value as AppLocale) : 'en',
  set: (v: AppLocale) => setLocale(v),
})

async function signOut(): Promise<void> {
  await session.logout()
  await router.push('/signin')
}
</script>

<template>
  <div class="shell">
    <a class="skip-link" href="#main">{{ t('nav.skip') }}</a>
    <header class="shell-top">
      <span class="brand">{{ t('app.name') }}</span>
      <span class="muted row-end" data-testid="whoami">{{ t('shell.signedInAs', { name: session.me?.display_name || session.me?.email || '' }) }}</span>
      <Select v-model="current" :options="localeOptions" option-label="label" option-value="code" :aria-label="t('shell.language')" data-testid="locale" />
      <Button :label="theme === 'dark' ? t('shell.light') : t('shell.dark')" severity="secondary" text data-testid="theme" @click="toggle" />
      <BellMenu />
      <Button :label="t('shell.signOut')" severity="secondary" text data-testid="signout" @click="signOut" />
    </header>
    <nav class="shell-nav" :aria-label="t('nav.label')">
      <ul>
        <li v-for="item in nav" :key="item.path">
          <router-link class="nav-link" :to="item.path">{{ item.label }}</router-link>
        </li>
      </ul>
    </nav>
    <main id="main" class="shell-main"><slot /></main>
  </div>
</template>
