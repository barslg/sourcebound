import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'
import { concrete, enter, expectedLanding, mustReach, text, type Role, type Session } from './t04h-support'
import { BACKEND_HREF_PREFIXES, EXTERNAL_ORIGINS, matchRow, ROWS } from '../src/router/registry'

const CAP = 260 // 19 seeded courses × their tabs sit before the preview topic pages in the crawl

interface Visit { path: string; name: string; problems: string[]; hrefs: string[] }

async function hrefsOf(page: Page): Promise<string[]> {
  return page.evaluate(() => [...document.querySelectorAll('a[href]')].map((a) => (a as HTMLAnchorElement).getAttribute('href') ?? ''))
}

async function hasWayOut(page: Page): Promise<boolean> {
  const way = await page.evaluate(() => ({
    crumbLinks: document.querySelectorAll('[data-testid="crumbs"] a').length,
    navLinks: document.querySelectorAll('.shell-nav a').length,
    shell: document.querySelector('.shell') !== null,
    anyLink: document.querySelectorAll('main a[href], main button:not([disabled])').length,
  }))
  return way.crumbLinks > 0 || (way.shell && way.navLinks > 0) || (!way.shell && way.anyLink > 0)
}

async function visit(page: Page, path: string): Promise<Visit> {
  await page.goto(path)
  await page.waitForLoadState('networkidle')
  await expect(page.locator('[data-view="loading"]')).toHaveCount(0)
  const landed = new URL(page.url()).pathname
  const problems: string[] = []
  const row = matchRow(landed)
  const unknown = !row
  if (unknown) problems.push(`${landed} is not a registered route`)
  const help = (await page.getByTestId('page-help').first().textContent().catch(() => null))?.trim() ?? ''
  if (row && help === text('notFound.help')) problems.push(`${landed} rendered the not-found page`)
  if (row?.parent) {
    const ancestors = new Set<string>()
    for (let p: string | null = row.parent; p; p = ROWS.find((x) => x.name === p)?.parent ?? null) ancestors.add(p)
    const crumbHrefs = await page.evaluate(() => [...document.querySelectorAll('[data-testid="crumbs"] a')].map((a) => new URL((a as HTMLAnchorElement).href).pathname))
    if (!crumbHrefs.some((h) => ancestors.has(matchRow(h)?.name ?? ''))) problems.push(`${landed} has a parent (${row.parent}) but no breadcrumb link back to it`)
  }
  if (!(await hasWayOut(page))) problems.push(`${landed} is a dead end: no breadcrumb, no navigation and no control`)
  const hrefs = await hrefsOf(page)
  const bell = page.getByTestId('bell')
  if (await bell.count()) {
    await bell.click()
    hrefs.push(...(await hrefsOf(page)))
    await page.keyboard.press('Escape')
  }
  const preview = page.locator('[data-action="preview"]:not([disabled])').first()
  if (await preview.count()) {
    await preview.click()
    await page.waitForURL(/\/preview/)
    hrefs.push(new URL(page.url()).pathname)
  }
  return { path: landed, name: row?.name ?? '?', problems, hrefs }
}

const ROLES: Role[] = ['admin', 'author', 'student', 'anonymous', 'waiting', 'editonly']
const matrix: string[] = []

for (const role of ROLES) {
  test(`reachability: ${role} reaches every route registered for it, every link resolves, no dead end, no 404`, async ({ page, context }) => {
    test.setTimeout(240000)
    const who: Session = await enter(context, page, role)
    const notFound404: string[] = []
    page.on('response', (r) => { if (r.status() === 404) notFound404.push(r.url()) })
    const queue: string[] = ['/']
    const seen = new Set<string>()
    const reached = new Set<string>()
    const problems: string[] = []
    while (queue.length > 0 && seen.size < CAP) {
      const next = queue.shift() as string
      if (seen.has(next)) continue
      seen.add(next)
      const v = await visit(page, next)
      seen.add(v.path)
      reached.add(v.name)
      problems.push(...v.problems.map((p) => `${role}: ${p}`))
      for (const raw of v.hrefs) {
        if (raw.startsWith('#') || raw === '') continue
        const url = new URL(raw, `http://127.0.0.1${v.path}`)
        const external = /^[a-z][a-z0-9+.-]*:/i.test(raw) && !raw.startsWith('http://127.0.0.1')
        if (external) {
          if (!EXTERNAL_ORIGINS.some((o) => raw.startsWith(o))) problems.push(`${role}: ${v.path} links to ${raw}, an origin that is not allowed`)
          continue
        }
        if (BACKEND_HREF_PREFIXES.some((p) => url.pathname.startsWith(p))) continue
        if (!matchRow(url.pathname)) problems.push(`${role}: ${v.path} links to ${url.pathname}, which is not a registered route`)
        else if (!seen.has(url.pathname)) queue.push(url.pathname)
      }
    }
    const must = mustReach(who)
    const missing = must.filter((r) => !reached.has(r.name)).map((r) => r.name)
    const leaked = [...reached].filter((n) => ROWS.find((r) => r.name === n && expectedLanding(r, who) !== null && r.access.kind !== 'public'))
    matrix.push(`${role}: reached ${must.length - missing.length}/${must.length} registered routes (${[...reached].sort().join(', ')})`)
    test.info().annotations.push({ type: 'matrix', description: matrix[matrix.length - 1] ?? '' })
    console.log(matrix[matrix.length - 1])
    expect(problems).toEqual([])
    expect(missing, `${role} cannot reach`).toEqual([])
    expect(leaked, `${role} reached a route it is not allowed`).toEqual([])
    expect(notFound404, '404 responses').toEqual([])
  })

  test(`reachability: ${role} is sent to ${role === 'anonymous' ? 'sign in' : 'the first allowed page'} from every route it may not open, without a flash of the page`, async ({ page, context }) => {
    test.setTimeout(120000)
    const who = await enter(context, page, role)
    await page.addInitScript(() => {
      const w = window as unknown as { __seen: string[] }
      w.__seen = []
      const note = (): void => { w.__seen.push(document.title, ...[...document.querySelectorAll('h1')].map((h) => h.textContent ?? '')) }
      new MutationObserver(note).observe(document, { subtree: true, childList: true, characterData: true })
    })
    const problems: string[] = []
    const denied = ROWS.filter((r) => !r.redirect && r.name !== 'not-found' && expectedLanding(r, who) !== null)
    for (const row of denied) {
      await page.goto(concrete(row))
      await page.waitForLoadState('networkidle')
      const landed = new URL(page.url()).pathname
      const want = expectedLanding(row, who)
      if (landed !== want) problems.push(`${role} asked ${row.name}, landed ${landed}, wanted ${want}`)
      const seenText = await page.evaluate(() => (window as unknown as { __seen: string[] }).__seen)
      const title = text(row.titleKey as string)
      const flash = seenText.filter((s) => s.trim().startsWith(title))
      if (flash.length > 0 ) problems.push(`${role} saw "${flash[0]}" of ${row.name} before the redirect`)
    }
    test.info().annotations.push({ type: 'matrix', description: `${role}: ${denied.length} denied routes all redirected` })
    expect(problems).toEqual([])
  })
}

test('reachability: a route entered from a live operation is linked from that operation result in the bell', async ({ page, context }) => {
  await enter(context, page, 'admin')
  const rows = ROWS.filter((r) => r.entry === 'operation')
  expect(rows.map((r) => r.name)).toEqual(['run'])
  for (const row of rows) {
    const target = concrete(row)
    const runId = target.split('/').pop() as string
    await page.route('**/api/v1/events', (route) => route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'data: {"type":"operation","operation_id":"op-reach","status":"done"}\n\n' }))
    await page.route('**/api/v1/operations/op-reach', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ operation_id: 'op-reach', kind: 'generate', status: 'done', progress: 1, result: { run_id: runId }, error: null }) }))
    await page.goto('/courses')
    await page.getByTestId('bell').click()
    await page.getByRole('link', { name: text('plan.openRun') }).first().click()
    await page.waitForLoadState('networkidle')
    expect(new URL(page.url()).pathname).toBe(target)
    expect(matchRow(new URL(page.url()).pathname)?.name).toBe(row.name)
  }
})

test('reachability: a page entered from outside (invite link, mistyped address) still has a way out and a purpose line', async ({ page, context }) => {
  const problems: string[] = []
  for (const row of ROWS.filter((r) => r.entry === 'external')) {
    const role: Role = row.name === 'not-found' ? 'admin' : 'anonymous'
    await enter(context, page, role)
    await page.goto(concrete(row))
    await page.waitForLoadState('networkidle')
    if (!(await hasWayOut(page))) problems.push(`${row.name} is a dead end`)
    const help = (await page.getByTestId('page-help').first().textContent())?.trim()
    if (help !== text(row.purposeKey as string)) problems.push(`${row.name} purpose line is "${help}"`)
    await context.clearCookies()
  }
  expect(problems).toEqual([])
})
