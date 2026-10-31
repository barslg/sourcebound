import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { decide, type SectionEntry } from './guard'

declare module 'vue-router' {
  interface RouteMeta {
    perm?: string
    public?: boolean
    navKey?: string
    bare?: boolean
  }
}

export const routes: RouteRecordRaw[] = [
  { path: '/signin', component: () => import('@/views/SignInView.vue'), meta: { public: true, bare: true } },
  { path: '/invite/:token', component: () => import('@/views/InviteAcceptView.vue'), meta: { public: true, bare: true } },
  { path: '/waiting', component: () => import('@/views/WaitingView.vue'), meta: { bare: true } },
  { path: '/', redirect: '/courses', meta: {} },
  { path: '/courses', component: () => import('@/views/CoursesView.vue'), meta: { perm: 'section.courses', navKey: 'nav.courses' } },
  { path: '/users', component: () => import('@/views/UsersView.vue'), meta: { perm: 'user.list', navKey: 'nav.users' } },
  { path: '/invites', component: () => import('@/views/InvitesView.vue'), meta: { perm: 'invite.create', navKey: 'nav.invites' } },
  { path: '/roles', component: () => import('@/views/RolesView.vue'), meta: { perm: 'role.edit', navKey: 'nav.roles' } },
  { path: '/:rest(.*)*', component: () => import('@/views/NotFoundView.vue'), meta: {} },
]

export function sectionEntries(): SectionEntry[] {
  return routes.filter((r) => r.meta?.perm && r.meta.navKey).map((r) => ({ path: r.path, perm: r.meta?.perm as string }))
}

export const router = createRouter({ history: createWebHistory(), routes })

router.beforeEach(async (to) => {
  const session = useSessionStore()
  if (!session.ready) await session.load()
  const target = to.path === '/' ? { path: '/', meta: {} } : { path: to.path, meta: { perm: to.meta.perm, public: to.meta.public } }
  const verdict = decide(
    target,
    { signedIn: session.signedIn, isSuperadmin: session.isSuperadmin, permissions: session.permissions },
    sectionEntries(),
  )
  return verdict === true ? true : verdict === to.path ? true : verdict
})
