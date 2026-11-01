import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'
import en from '../../src/i18n/locales/en.json'
import nb from '../../src/i18n/locales/nb.json'
import uk from '../../src/i18n/locales/uk.json'
import { routes } from '../../src/router/routes'
import { matchRow, navEntries, pathFor, REGISTRY, ROWS, STAGE_TO_ROUTE, trail } from '../../src/router/registry'
import { ACTION_IDS } from '../../src/ui/actionMeta'
import { stages, type StageFacts } from '../../src/composables/needsNow'

const SRC = join(__dirname, '../../src')
const CATALOGUE = join(__dirname, '../../../src/app/rbac/catalogue.py')

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name)
    return statSync(p).isDirectory() ? files(p) : /\.(vue|ts)$/.test(p) ? [p] : []
  })
}
const all = files(SRC).map((p) => ({ rel: relative(SRC, p), text: readFileSync(p, 'utf8') }))
const lookup = (tree: unknown, key: string): unknown => key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], tree)
function must<T>(v: T | undefined | null): T {
  if (v === undefined || v === null) throw new Error('expected a value')
  return v
}
const pages = ROWS.filter((r) => !r.redirect)

describe('route registry: the router is built from it and nothing else', () => {
  it('builds exactly the registered routes, with the registered permission and visibility', () => {
    expect(routes.map((r) => [r.name, r.path])).toEqual(ROWS.map((r) => [r.name, r.path]))
    for (const r of routes) {
      const row = must(ROWS.find((x) => x.name === r.name))
      expect(Boolean(r.redirect) || Boolean(r.component), `${String(r.name)} has a view`).toBe(true)
      expect(r.meta?.perm, String(r.name)).toBe(row.access.kind === 'perm' ? row.access.code : undefined)
      expect(r.meta?.public === true, String(r.name)).toBe(row.access.kind === 'public')
    }
  })
  it('defines no route outside the registry', () => {
    const literal = /\bpath:\s*['"`]\/[^'"`]*['"`],\s*(component|redirect|name|children)\b/
    expect(all.filter((f) => literal.test(f.text) && f.rel !== 'router/registry.ts').map((f) => f.rel)).toEqual([])
    expect(all.filter((f) => /\.addRoute\(|createRouter\(/.test(f.text) && f.rel !== 'router/index.ts').map((f) => f.rel)).toEqual([])
    const index = must(all.find((f) => f.rel === 'router/index.ts')).text
    expect(index).toMatch(/routes,\n/)
  })
  it('gives every view file a route and every route a view file', () => {
    const views = must(all.find((f) => f.rel === 'router/views.ts')).text
    const mapped = [...views.matchAll(/'@\/views\/([^']+)'/g)].map((m) => m[1]).sort()
    const onDisk = all.filter((f) => f.rel.startsWith('views/')).map((f) => f.rel.slice('views/'.length)).sort()
    expect(mapped).toEqual(onDisk)
  })
  it('has unique names and paths, real parents and no parent cycle', () => {
    expect(new Set(ROWS.map((r) => r.name)).size).toBe(ROWS.length)
    expect(new Set(ROWS.map((r) => r.path)).size).toBe(ROWS.length)
    for (const r of ROWS) {
      let hops = 0
      for (let p = r.parent; p; p = ROWS.find((x) => x.name === p)?.parent ?? null) {
        expect(ROWS.some((x) => x.name === p), `${r.name} parent ${p}`).toBe(true)
        expect(++hops, `${r.name} parent chain loops`).toBeLessThan(ROWS.length)
      }
    }
  })
  it('names the object in the path: :id for a course, :runId for a run, :token for an invite', () => {
    const want = { course: ':id', learn: ':id', run: ':runId', invite: ':token' } as const
    for (const r of pages.filter((x) => x.name !== 'not-found')) {
      const params = r.path.split('/').filter((s) => s.startsWith(':'))
      expect(params, r.name).toEqual(r.object in want ? [want[r.object as keyof typeof want]] : [])
    }
  })
})

describe('route registry: every row is complete in en, nb and uk', () => {
  it('has a title, a purpose and (in the main nav) a label key that exists in every locale', () => {
    const missing: string[] = []
    for (const r of pages) {
      const keys = [r.titleKey, r.purposeKey, r.nav?.section === 'main' ? r.nav.labelKey : 'x.y'].filter((k): k is string => k !== null)
      expect(r.titleKey && r.purposeKey, `${r.name} has title and purpose keys`).toBeTruthy()
      for (const [locale, tree] of Object.entries({ en, nb, uk })) for (const k of keys) if (k !== 'x.y' && typeof lookup(tree, k) !== 'string') missing.push(`${locale} ${r.name} ${k}`)
    }
    expect(missing).toEqual([])
  })
  it('takes every permission code from the backend catalogue (read from src/app/rbac/catalogue.py)', () => {
    expect(existsSync(CATALOGUE), 'catalogue file').toBe(true)
    const codes = new Set([...readFileSync(CATALOGUE, 'utf8').matchAll(/PermissionDef\(\s*"([a-z]+\.[a-z]+)"/g)].map((m) => m[1]))
    expect(codes.size).toBeGreaterThan(20)
    const used = ROWS.flatMap((r) => (r.access.kind === 'perm' ? [r.access.code] : []))
    expect(used.length).toBeGreaterThan(5)
    expect(used.filter((c) => !codes.has(c))).toEqual([])
  })
  it('names a primary action from the action map', () => {
    const bad = ROWS.filter((r) => r.primaryAction !== null && !ACTION_IDS.includes(r.primaryAction)).map((r) => r.name)
    expect(bad).toEqual([])
    expect(ROWS.filter((r) => r.primaryAction !== null).length).toBeGreaterThan(8)
  })
  it('declares the queued state exactly where a view draws the Queued component', () => {
    const views = must(all.find((f) => f.rel === 'router/views.ts')).text
    const file = new Map([...views.matchAll(/'?([\w-]+)'?:\s*\(\) => import\('@\/views\/([^']+)'\)/g)].map((m) => [m[1], m[2]]))
    const drawsQueued = (name: string): boolean => /<Queued\b/.test(all.find((f) => f.rel === `views/${file.get(name)}`)?.text ?? '')
    expect(pages.filter((r) => drawsQueued(r.name)).map((r) => r.name).sort()).toEqual(pages.filter((r) => r.states.includes('queued')).map((r) => r.name).sort())
  })
})

describe('route registry: navigation and breadcrumbs read from it', () => {
  it('lists in the main nav only routes that need a section permission', () => {
    const main = navEntries('main')
    expect(main.map((r) => r.name)).toEqual(['courses', 'users', 'invites', 'roles', 'learn'])
    for (const r of main) expect(r.access.kind, r.name).toBe('perm')
  })
  it('maps every course stage to a registered course route and the course nav section to those routes', () => {
    const facts: StageFacts = { status: 'draft', profile: true, plan: true, openQuestions: 0, published: false, courseId: 'c', firstQuestionKey: null, sourceCount: 1, draft: 0, pendingSources: 0, firstPendingId: null, held: 0, firstHeldId: null, generated: true }
    const ids = stages(facts, 1).map((s) => s.id)
    expect(ids.filter((id) => !STAGE_TO_ROUTE[id])).toEqual([])
    const targets = new Set(Object.values(STAGE_TO_ROUTE))
    expect(navEntries('course').map((r) => r.name).sort()).toEqual([...targets].sort())
  })
  it('builds the breadcrumb from the parent chain', () => {
    const t = (k: string): string => `<${k}>`
    const ctx = { t, courseId: 'c1', courseTitle: 'Hygiene' }
    expect(trail('course-plan', ctx)).toEqual([{ label: '<courses.title>', to: '/courses' }, { label: 'Hygiene' }])
    expect(trail('course-preview', ctx)).toEqual([{ label: '<courses.title>', to: '/courses' }, { label: 'Hygiene', to: '/courses/c1/publish' }, { label: '<preview.title>' }])
    expect(trail('run', ctx)).toEqual([{ label: '<courses.title>', to: '/courses' }, { label: 'Hygiene', to: '/courses/c1/plan' }, { label: '<run.title>' }])
    expect(trail('run', { ...ctx, courseId: null, courseTitle: null })).toEqual([{ label: '<courses.title>', to: '/courses' }, { label: '<run.title>' }])
  })
  it('keeps the path builder and the matcher inverse', () => {
    for (const r of pages.filter((x) => x.name !== 'not-found')) {
      const path = pathFor(r.name, { id: 'abc', runId: 'r1', token: 'tok' })
      expect(matchRow(path)?.name, r.name).toBe(r.name)
    }
    expect(matchRow('/nowhere')).toBeNull()
    expect(REGISTRY.length).toBe(ROWS.length)
  })
})

describe('route registry: every internal link written in src points at a registered route', () => {
  const found: Array<{ rel: string; path: string }> = []
  const norm = (raw: string): string => {
    let s = raw.replace(/^\$\{base\}/, '/courses/${x}').replace(/\$\{[^}]*\}/g, 'X')
    s = must(s.split('#')[0]).split('?')[0] ?? ''
    return s.length > 1 ? s.replace(/\/$/, '') : s
  }
  for (const f of all) {
    const patterns = [
      /\bto="(\/[^"]*)"/g,
      /:to="`([^`]*)`"/g,
      /:to="'(\/[^']*)'"/g,
      /router\.push\(`([^`]*)`\)/g,
      /router\.push\('(\/[^']*)'\)/g,
      /\bhref="(\/[^"]*)"/g,
      /to: `(\$\{base\}[^`]*)`/g,
    ]
    for (const re of patterns) for (const m of f.text.matchAll(re)) { const raw = m[1] ?? ''; if (raw.startsWith('/') || raw.startsWith('${base}')) found.push({ rel: f.rel, path: norm(raw) }) }
  }
  it('finds the links (the scan is not empty) and resolves each one', () => {
    expect(found.length).toBeGreaterThan(25)
    const bad = found.filter((l) => !matchRow(l.path)).map((l) => `${l.rel}: ${l.path}`)
    expect(bad).toEqual([])
  })
})
