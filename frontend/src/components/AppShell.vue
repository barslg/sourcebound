<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { BookOpen, GraduationCap, Circle, CircleCheck, CircleDot, Lock, Share2, Shield, Users } from 'lucide-vue-next'
import Select from 'primevue/select'
import ActionButton from '@/components/ActionButton.vue'
import BellMenu from '@/components/BellMenu.vue'
import ShortcutSheet from '@/components/ShortcutSheet.vue'
import { LOCALES, setLocale, type AppLocale } from '@/i18n'
import { navEntries, pathFor, allows } from '@/router/registry'
import { useSessionStore } from '@/stores/session'
import { useCourseContext } from '@/stores/courseContext'
import { useShortcuts } from '@/composables/useShortcuts'
import { useTheme } from '@/composables/useTheme'

const { t, locale } = useI18n()
const session = useSessionStore()
const router = useRouter()
const route = useRoute()
const ctx = useCourseContext()
const { theme, toggle } = useTheme()
const { sheet } = useShortcuts()
const STATE_ICON = { done: CircleCheck, needs: CircleDot, blocked: Lock, later: Circle } as const

const NAV_ICONS = { courses: BookOpen, users: Users, invites: Share2, roles: Shield, learn: GraduationCap } as const
const nav = computed(() =>
  navEntries('main')
    .filter((r) => allows(r, { signedIn: session.signedIn, isSuperadmin: session.isSuperadmin, permissions: session.permissions }))
    .map((r) => ({ path: pathFor(r.name), label: t(r.nav?.labelKey as string), icon: NAV_ICONS[r.nav?.icon ?? 'courses'] })),
)
const localeOptions = computed(() => LOCALES.map((code) => ({ code, label: t(`locale.${code}`) })))
const current = computed({
  get: () => (LOCALES as readonly string[]).includes(locale.value) ? (locale.value as AppLocale) : 'en',
  set: (v: AppLocale) => setLocale(v),
})
const inCourse = computed(() => ctx.courseId !== null && typeof route.params.id === 'string')

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
      <ActionButton action="theme" icon-only :label="theme === 'dark' ? t('shell.toLight') : t('shell.toDark')" data-testid="theme" @click="toggle" />
      <BellMenu />
      <ActionButton action="signout" :label="t('shell.signOut')" data-testid="signout" @click="signOut" />
    </header>
    <nav class="shell-nav" :aria-label="t('nav.label')">
      <ul>
        <li v-for="item in nav" :key="item.path">
          <router-link class="nav-link" :to="item.path"><component :is="item.icon" :size="20" :stroke-width="2" aria-hidden="true" />{{ item.label }}</router-link>
        </li>
      </ul>
      <template v-if="inCourse">
        <p class="nav-heading">{{ t('nav.thisCourse') }}</p>
        <ul>
          <li><router-link class="nav-link" :to="`/courses/${ctx.courseId}/plan`" data-testid="nav-course"><component :is="CircleDot" :size="20" :stroke-width="2" aria-hidden="true" />{{ ctx.title }}</router-link></li>
          <li v-for="s in ctx.stages" :key="s.id">
            <router-link class="nav-stage" :to="s.to" :aria-current-value="s.id === 'generate' ? 'false' : 'page'" data-testid="nav-stage"><component :is="STATE_ICON[s.state]" :size="16" :stroke-width="2" aria-hidden="true" />{{ t(`course.stage.${s.id}`) }}<span class="sr-only"> {{ t(`course.state.${s.state}`) }}</span></router-link>
          </li>
        </ul>
      </template>
    </nav>
    <main id="main" class="shell-main"><slot /></main>
    <footer class="shell-foot">
      <span>{{ t('shortcuts.footer') }}</span>
      <ActionButton action="shortcuts" size="small" class="row-end" @click="sheet = true" />
    </footer>
    <ShortcutSheet v-model:visible="sheet" />
  </div>
</template>
