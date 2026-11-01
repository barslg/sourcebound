import type { RouteRecordRaw } from 'vue-router'
import { ROWS, type RouteRow } from './registry'
import { VIEWS } from './views'

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
