import type { Page } from '@playwright/test'
import { expect, ids, test } from './fixtures'
import { enter, TOKEN, text, type Role } from './t04h-support'
import { matchRow, ROWS, rowOf, type RouteRow } from '../src/router/registry'
import { ACTION_IDS } from '../src/ui/actionMeta'

interface Instance { who: Role; path: () => string; before?: (page: Page) => Promise<void> }
const I = (): ReturnType<typeof ids> => ids()
/** One real screen per registered route, on which its primary action is on show. A new route without an entry here fails the first test. */
const INSTANCES: Record<string, Instance> = {
  signin: { who: 'anonymous', path: () => '/signin' },
  invite: { who: 'anonymous', path: () => `/invite/${TOKEN}` },
  waiting: { who: 'waiting', path: () => '/waiting' },
  courses: { who: 'admin', path: () => '/courses' },
  'course-sources': { who: 'admin', path: () => `/courses/${I().course}/sources` },
  'course-plan': { who: 'admin', path: () => `/courses/${I().course}/plan` },
  'course-fix': {
    who: 'admin',
    path: () => `/courses/${I().fix}/fix`,
    before: async (page) => { await page.route(`**/api/v1/courses/${I().fix}/held`, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ blocks: [{ id: 'b-1', topic_title: 'Basics', brief: 'Why?', hold_reason: 'Not in the passage.', passage: 'Alcohol rub takes twenty seconds.', address: 'para1' }], batch_quote_usd: '0.020000' }) })) },
  },
  'course-preview': { who: 'admin', path: () => `/courses/${I().translated}/preview` },
  'course-publish': { who: 'admin', path: () => `/courses/${I().publish}/publish` },
  learn: { who: 'student', path: () => '/learn' },
  'course-runs': { who: 'admin', path: () => `/courses/${I().course}/runs` },
  run: { who: 'admin', path: () => `/runs/${I().run}` },
  users: { who: 'admin', path: () => '/users' },
  invites: { who: 'admin', path: () => '/invites' },
  roles: { who: 'admin', path: () => '/roles' },
  'not-found': { who: 'admin', path: () => '/no-such-page-at-all' },
}
const PAGES = ROWS.filter((r) => !r.redirect)

/** Buttons that are not ActionButtons: disclosure toggles, PrimeVue parts and ARIA widgets that carry their own role. */
const OWN_ROLE = '[aria-expanded][aria-controls], [role="tab"], [role="combobox"], [role="switch"], [role="checkbox"], [role="radio"], .p-paginator button, .p-tree button, .p-select, .p-datatable-sort-icon, .p-datatable button, .p-multiselect, .p-toast button, .p-popover button, .p-dialog-header button, .p-inputnumber button'

async function lookupTitles(page: Page, row: RouteRow, path: string): Promise<{ course: string | null }> {
  if (row.object === 'course') {
    const id = path.split('/')[2] as string
    return { course: ((await (await page.request.get(`/api/v1/courses/${id}`)).json()) as { title: string }).title }
  }
  if (row.object === 'run') {
    const run = (await (await page.request.get(path.replace('/runs/', '/api/v1/runs/'))).json()) as { course_id: string | null }
    if (!run.course_id) return { course: null }
    return { course: ((await (await page.request.get(`/api/v1/courses/${run.course_id}`)).json()) as { title: string }).title }
  }
  return { course: null }
}

test('grammar: every registered route has a screen in this walk', () => {
  expect(PAGES.map((r) => r.name).filter((n) => !(n in INSTANCES))).toEqual([])
  expect(Object.keys(INSTANCES).filter((n) => !PAGES.some((r) => r.name === n))).toEqual([])
})

test('grammar: purpose line, title, heading, breadcrumb and primary action of every route equal the registry', async ({ page, context }) => {
  test.setTimeout(180000)
  const problems: string[] = []
  for (const row of PAGES) {
    const inst = INSTANCES[row.name] as Instance
    await context.clearCookies()
    await enter(context, page, inst.who)
    const path = inst.path()
    await page.unrouteAll()
    await inst.before?.(page)
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    await expect(page.locator('[data-view="loading"]')).toHaveCount(0)
    const tag = (msg: string): void => { problems.push(`${row.name}: ${msg}`) }
    const { course } = await lookupTitles(page, row, path)
    const ancestors: RouteRow[] = []
    for (let p = row.parent; p; p = rowOf(p).parent) ancestors.unshift(rowOf(p))

    const help = (await page.getByTestId('page-help').first().textContent())?.trim()
    if (help !== text(row.purposeKey as string)) tag(`purpose line is "${help}", registry says "${text(row.purposeKey as string)}"`)

    const wantTitle = [text(row.titleKey as string), row.object === 'course' ? course : null, 'Sourcebound'].filter(Boolean).join(' · ')
    if (await page.title() !== wantTitle) tag(`browser title is "${await page.title()}", registry says "${wantTitle}"`)

    if (row.name !== 'signin') {
      const wantH1 = row.heading === 'object' ? course : text(row.titleKey as string)
      const h1 = (await page.locator('h1').first().textContent())?.trim()
      if (h1 !== wantH1) tag(`heading is "${h1}", registry says "${wantH1}"`)
    }

    const crumbs = await page.evaluate(() => [...document.querySelectorAll('[data-testid="crumbs"] li')].map((li) => ({ label: (li.textContent ?? '').trim(), href: li.querySelector('a')?.getAttribute('href') ?? null })))
    if (row.parent === null) {
      if (crumbs.length > 0) tag(`has a breadcrumb but no parent in the registry`)
    } else {
      const want = [...ancestors.map((a) => (a.object === 'course' ? course : text(a.titleKey as string))), row.heading === 'object' ? course : text(row.titleKey as string)]
      if (JSON.stringify(crumbs.map((c) => c.label)) !== JSON.stringify(want)) tag(`breadcrumb is ${JSON.stringify(crumbs.map((c) => c.label))}, parent chain says ${JSON.stringify(want)}`)
      const names = crumbs.slice(0, -1).map((c) => (c.href ? matchRow(c.href)?.name : '(no link)'))
      if (JSON.stringify(names) !== JSON.stringify(ancestors.map((a) => a.name))) tag(`breadcrumb links go to ${JSON.stringify(names)}, parent chain says ${JSON.stringify(ancestors.map((a) => a.name))}`)
      if (crumbs[crumbs.length - 1]?.href) tag('the current page is a link in its own breadcrumb')
    }

    if (row.primaryAction) {
      const mine = page.locator(`[data-action="${row.primaryAction}"]`)
      const n = await mine.count()
      if (n === 0) tag(`primary action "${row.primaryAction}" is not on the page`)
      else {
        const first = mine.first()
        if (await first.isDisabled()) {
          const reason = (await first.locator('xpath=ancestor::span[contains(@class,"guarded")][1]').getByTestId('reason').first().textContent().catch(() => null))?.trim() ?? ''
          if (reason.length === 0) tag(`primary action "${row.primaryAction}" is disabled and says nothing`)
        }
      }
    }

    const audit = await page.evaluate((own) => {
      const seen = (el: Element): boolean => (el as HTMLElement).checkVisibility()
      const actions = [...document.querySelectorAll('[data-action]')].map((e) => e.getAttribute('data-action') ?? '')
      const loose = [...document.querySelectorAll('button, a[role="button"]')].filter((b) => seen(b) && !b.hasAttribute('data-action') && !b.matches(own) && !b.closest(own)).map((b) => (b.getAttribute('aria-label') ?? (b as HTMLElement).innerText ?? '').trim().slice(0, 40))
      return { actions, loose }
    }, OWN_ROLE)
    for (const a of audit.actions) if (!ACTION_IDS.includes(a)) tag(`renders a control with action id "${a}", which is not in the action map`)
    for (const l of audit.loose) tag(`renders a button "${l}" that has no action id`)
  }
  expect(problems).toEqual([])
})
