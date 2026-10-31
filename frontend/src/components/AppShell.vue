<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { request } from '@/api/client'
import { courseItem } from '@/api/endpoints'
import type { CourseDoc } from '@/api/types'
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
const route = useRoute()
const courseId = computed(() => (typeof route.params.id === 'string' ? route.params.id : null))
const courseTitle = ref('')
watch(courseId, async (id) => {
  courseTitle.value = ''
  if (!id) return
  try { courseTitle.value = (await request<CourseDoc>(courseItem(id))).title } catch { courseTitle.value = t('nav.thisCourse') }
}, { immediate: true })

const nav = computed(() =>
  routes
    .filter((r) => r.meta?.navKey && r.meta.perm && session.can(r.meta.perm))
    .map((r) => ({ path: r.path, label: t(r.meta?.navKey as string), icon: r.meta?.icon ?? 'pi pi-circle' })),
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
      <router-link class="brand" to="/">{{ t('app.name') }}</router-link>
      <router-link v-if="session.can('user.list')" class="muted row-end" data-testid="whoami" :to="`/users?q=${encodeURIComponent(session.me?.email ?? '')}`">{{ t('shell.signedInAs', { name: session.me?.display_name || session.me?.email || '' }) }}</router-link>
      <span v-else class="muted row-end" data-testid="whoami">{{ t('shell.signedInAs', { name: session.me?.display_name || session.me?.email || '' }) }}</span>
      <Select v-model="current" :options="localeOptions" option-label="label" option-value="code" :aria-label="t('shell.language')" data-testid="locale" />
      <Button :label="theme === 'dark' ? t('shell.toLight') : t('shell.toDark')" severity="secondary" text data-testid="theme" @click="toggle" />
      <BellMenu />
      <Button :label="t('shell.signOut')" severity="secondary" text data-testid="signout" @click="signOut" />
    </header>
    <nav class="shell-nav" :aria-label="t('nav.label')">
      <ul>
        <li v-for="item in nav" :key="item.path">
          <router-link class="nav-link" :to="item.path"><i :class="item.icon" aria-hidden="true" /> {{ item.label }}</router-link>
        </li>
      </ul>
      <template v-if="courseId">
        <h2 class="nav-heading">{{ t('nav.thisCourse') }}</h2>
        <ul>
          <li><router-link class="nav-link" :to="`/courses/${courseId}/plan`" data-testid="nav-course"><i class="pi pi-bookmark" aria-hidden="true" /> {{ courseTitle }}</router-link></li>
        </ul>
      </template>
    </nav>
    <main id="main" class="shell-main"><slot /></main>
  </div>
</template>
