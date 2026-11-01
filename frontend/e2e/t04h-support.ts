import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { BrowserContext, Page } from '@playwright/test'
import { ids, signInAs, type Who } from './fixtures'
import { allows, CATCH_ALL, navEntries, pathFor, ROWS, type RouteRow } from '../src/router/registry'

export type Role = Who | 'anonymous'
export interface Session { signedIn: boolean; isSuperadmin: boolean; permissions: string[]; courses: number }

export async function enter(context: BrowserContext, page: Page, role: Role): Promise<Session> {
  if (role === 'anonymous') return { signedIn: false, isSuperadmin: false, permissions: [], courses: 0 }
  await signInAs(context, role)
  const me = (await (await page.request.get('/api/v1/auth/me')).json()) as { permissions: string[]; is_superadmin: boolean }
  const list = await page.request.get('/api/v1/courses')
  const courses = list.ok() ? ((await list.json()) as unknown[]).length : 0
  return { signedIn: true, isSuperadmin: me.is_superadmin, permissions: me.permissions, courses }
}

export const TOKEN = 'ab'.repeat(32)

/** One real address for a registered route, built from the seeded ids. */
export function concrete(row: RouteRow): string {
  if (row.name === CATCH_ALL) return '/no-such-page-at-all'
  const i = ids()
  return pathFor(row.name, { id: i.course, runId: i.run, token: TOKEN })
}

/** Where the app must send this session when it asks for the row: an independent oracle, not the guard itself. */
export function expectedLanding(row: RouteRow, who: Session): string | null {
  const noRights = !who.isSuperadmin && who.permissions.length === 0
  if (row.access.kind === 'public') return who.signedIn && row.name === 'signin' ? expectedHome(who) : null
  if (!who.signedIn) return '/signin'
  if (row.name === 'waiting') return noRights || expectedHome(who) === '/waiting' ? null : expectedHome(who)
  if (noRights) return '/waiting'
  return allows(row, who) ? null : expectedHome(who)
}

export function expectedHome(who: Session): string {
  if (!who.signedIn) return '/signin'
  const first = navEntries('main').find((r) => allows(r, who))
  return first ? first.path : '/waiting'
}

/** Pages the role must reach by following links: allowed, not a redirect, not an address that arrives from outside or from a live operation, and not an object page when the role sees no course. */
export function mustReach(who: Session): RouteRow[] {
  return ROWS.filter((r) => !r.redirect && r.entry === 'ui' && expectedLanding(r, who) === null && !(r.object === 'course' && who.courses === 0))
}

const EN: unknown = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../src/i18n/locales/en.json'), 'utf8'))
/** The English text behind an i18n key, read straight from the locale file. */
export function text(key: string): string {
  const found = key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], EN)
  if (typeof found !== 'string') throw new Error(`no English text for ${key}`)
  return found
}
