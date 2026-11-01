export interface SectionEntry { path: string; perm: string }
export interface GuardSession { signedIn: boolean; isSuperadmin: boolean; permissions: string[] }
export interface GuardTarget { path: string; meta: { perm?: string; public?: boolean } }

const has = (s: GuardSession, code: string): boolean => s.isSuperadmin || s.permissions.includes(code)

export function firstAllowed(s: GuardSession, sections: SectionEntry[]): string | null {
  return sections.find((entry) => has(s, entry.perm))?.path ?? null
}

export function decide(to: GuardTarget, s: GuardSession, sections: SectionEntry[]): true | string {
  const home = firstAllowed(s, sections)
  const noRights = !s.isSuperadmin && s.permissions.length === 0
  if (to.meta.public) {
    return s.signedIn && to.path === '/signin' ? (noRights ? '/waiting' : (home ?? '/waiting')) : true
  }
  if (!s.signedIn) return '/signin'
  if (to.path === '/waiting') return noRights || home === null ? true : home
  if (noRights) return '/waiting'
  if (to.path === '/') return home ?? '/waiting'
  if (to.meta.perm && !has(s, to.meta.perm)) return home ?? '/waiting'
  return true
}
