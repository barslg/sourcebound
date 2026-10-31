import { createRouter, createWebHistory } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { decide } from './guard'
import { routes, sectionEntries } from './routes'

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: (to) => (to.hash ? { el: to.hash, behavior: 'smooth' } : undefined),
})

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
