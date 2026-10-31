import type { RouteRecordRaw } from 'vue-router'
import { ROWS, type RouteRow } from './registry'
import { VIEWS } from './views'
import type { SectionEntry } from './guard'

declare module 'vue-router' {
  interface RouteMeta {
    perm?: string
    public?: boolean
    bare?: boolean
  }
}

function meta(r: RouteRow): { perm?: string; public?: boolean; bare?: boolean } {
  return {
    ...(r.access.kind === 'perm' ? { perm: r.access.code } : {}),
    ...(r.access.kind === 'public' ? { public: true } : {}),
    ...(r.bare ? { bare: true } : {}),
  }
}

export const routes: RouteRecordRaw[] = ROWS.map((r): RouteRecordRaw =>
  r.redirect
    ? { path: r.path, name: r.name, redirect: r.redirect, meta: {} }
    : { path: r.path, name: r.name, component: VIEWS[r.name as keyof typeof VIEWS], meta: meta(r) },
)

export function sectionEntries(): SectionEntry[] {
  return ROWS.filter((r) => r.nav?.section === 'main' && r.access.kind === 'perm').map((r) => ({ path: r.path, perm: r.access.kind === 'perm' ? r.access.code : '' }))
}
