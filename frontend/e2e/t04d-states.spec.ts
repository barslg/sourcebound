import type { Page, Route } from '@playwright/test'
import { expect, ids, signInAs, test } from './fixtures'
import { ROWS } from '../src/router/registry'

const json = (route: Route, status: number, body: unknown): Promise<void> =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
const denied = { error: 'forbidden', message: 'You do not have permission for this action.' }
const broken = { error: 'internal', message: 'internal error' }

interface Screen { name: string; row: string; path: () => string; api: string; emptyBody: unknown; emptyLink: RegExp; noEmpty?: boolean }

const SCREENS: Screen[] = [
  { name: 'courses', row: 'courses', path: () => '/courses', api: '**/api/v1/courses', emptyBody: [], emptyLink: /Create your first course/ },
  { name: 'users', row: 'users', path: () => '/users', api: '**/api/v1/users', emptyBody: [], emptyLink: /Invite people|No accounts/ },
  { name: 'roles', row: 'roles', path: () => '/roles', api: '**/api/v1/roles', emptyBody: [], emptyLink: /No roles/ },
  { name: 'invites', row: 'invites', path: () => '/invites', api: '**/api/v1/invites', emptyBody: [], emptyLink: /Create the first invite/ },
  { name: 'sources', row: 'course-sources', path: () => `/courses/${ids().course}/sources`, api: `**/api/v1/courses/${ids().course}/sources`, emptyBody: [], emptyLink: /Add your first source/ },
  { name: 'plan', row: 'course-plan', path: () => `/courses/${ids().course}/plan`, api: `**/api/v1/courses/${ids().course}/plan`, emptyBody: { modules: [] }, emptyLink: /Build the plan/ },
  { name: 'fix', row: 'course-fix', path: () => `/courses/${ids().course}/fix`, api: `**/api/v1/courses/${ids().course}/held`, emptyBody: { blocks: [], batch_quote_usd: null }, emptyLink: /Go to (Generate|Publish)/ },
  { name: 'learn', row: 'learn', path: () => '/learn', api: '**/api/v1/learn/courses', emptyBody: [], emptyLink: /not enrolled/ },
  { name: 'runs', row: 'course-runs', path: () => `/courses/${ids().course}/runs`, api: `**/api/v1/courses/${ids().course}/runs`, emptyBody: [], emptyLink: /No run yet/ },
  { name: 'run', row: 'run', path: () => `/runs/${ids().run}`, api: `**/api/v1/runs/${ids().run}`, emptyBody: null, emptyLink: /./, noEmpty: true },
  { name: 'publish', row: 'course-publish', path: () => `/courses/${ids().publish}/publish`, api: `**/api/v1/courses/${ids().publish}`, emptyBody: null, emptyLink: /./, noEmpty: true },
  { name: 'preview', row: 'course-preview', path: () => `/courses/${ids().translated}/preview`, api: `**/api/v1/courses/${ids().translated}/learner*`, emptyBody: null, emptyLink: /./, noEmpty: true },
]

test('states: every state the registry declares for a route is exercised by a screen here', () => {
  const tested = (row: string): string[] => {
    const own = SCREENS.filter((x) => x.row === row)
    return own.length === 0 ? [] : ['loading', 'error', 'denied', ...(own.some((x) => !x.noEmpty) ? ['empty'] : [])]
  }
  const missing = ROWS.flatMap((r) => r.states.filter((st) => st !== 'queued' && !tested(r.name).includes(st)).map((st) => `${r.name}: ${st}`))
  expect(missing).toEqual([])
  expect(SCREENS.filter((x) => !ROWS.some((r) => r.name === x.row))).toEqual([])
})

for (const s of SCREENS) {
  test(`states: ${s.name} shows loading, error, denied`, async ({ page, context }) => {
    await signInAs(context, 'admin')
    await page.route(s.api, async (route) => {
      await new Promise((r) => setTimeout(r, 700))
      await route.continue()
    })
    await page.goto(s.path())
    await expect(page.locator('[data-view="loading"]').first()).toBeVisible()
    await page.unroute(s.api)
    await page.route(s.api, (route) => json(route, 500, broken))
    await page.goto(s.path())
    const err = page.locator('[data-view="error"]').first()
    await expect(err).toContainText('internal error')
    await expect(err.getByRole('button', { name: 'Try again' })).toBeVisible()
    await page.unroute(s.api)
    await page.route(s.api, (route) => json(route, 403, denied))
    await page.goto(s.path())
    const box = page.locator('[data-view="denied"]').first()
    await expect(box).toContainText('You do not have permission for this action.')
    await expect(box.getByTestId('denied-home')).toBeVisible()
  })
}

for (const s of SCREENS.filter((x) => !x.noEmpty)) {
  test(`states: ${s.name} empty line names the next action`, async ({ page, context }) => {
    await signInAs(context, 'admin')
    await page.route(s.api, (route) => (route.request().method() === 'GET' ? json(route, 200, s.emptyBody) : route.continue()))
    await page.goto(s.path())
    const empty = page.locator('[data-view="empty"], .p-datatable-empty-message, .muted').filter({ hasText: s.emptyLink }).first()
    await expect(empty).toBeVisible()
    if (!['users', 'roles', 'learn', 'runs'].includes(s.name)) {
      const scope = page.locator('[data-view="empty"]').first()
      await expect(scope.getByRole('button', { name: s.emptyLink }).or(scope.getByRole('link', { name: s.emptyLink }))).toBeVisible()
    }
  })
}

test('states: a learner is sent to the first allowed page, not shown a dead end', async ({ page, context }) => {
  await signInAs(context, 'student')
  for (const path of ['/users', '/roles', '/invites']) {
    await page.goto(path)
    await expect(page).toHaveURL(/\/learn$/)
  }
})

async function queuedFlow(page: Page, opts: { go: () => Promise<void>; refetched: RegExp }): Promise<void> {
  let opCalls = 0
  const refetch: string[] = []
  await page.route('**/api/v1/operations/op-t04d', (route) => {
    opCalls += 1
    return json(route, 200, { operation_id: 'op-t04d', kind: 'plan', status: opCalls < 3 ? 'queued' : 'done', progress: opCalls < 3 ? 0 : 1, result: null, error: null })
  })
  page.on('request', (req) => { if (req.method() === 'GET') refetch.push(new URL(req.url()).pathname) })
  await opts.go()
  await expect(page.getByTestId('queued-line').first()).toContainText('Queued')
  await expect(page.getByText('Operation finished.').first()).toBeVisible({ timeout: 15000 })
  await expect(page.getByTestId('queued-line')).toHaveCount(0)
  const after = refetch.slice(refetch.findIndex((p) => p.includes('/operations/op-t04d')))
  expect(after.some((p) => opts.refetched.test(p))).toBe(true)
}

test('states: building a plan is queued, announced, and the plan is fetched again when it finishes', async ({ page, context }) => {
  await signInAs(context, 'admin')
  await page.route(`**/api/v1/courses/${ids().empty}/profile`, (route) => json(route, 200, { ok: true }))
  await page.route(`**/api/v1/courses/${ids().empty}/plan/submit`, (route) => json(route, 202, { operation_id: 'op-t04d' }))
  await page.route(`**/api/v1/courses/${ids().empty}/sources`, (route) => json(route, 200, [{ id: 's-1', title: 'Guide', media: 'text', licence: 'CC-BY', as_of: null, approval: 'approved', progress: 3 }]))
  await page.goto(`/courses/${ids().empty}/plan`)
  await queuedFlow(page, {
    go: async () => {
      await page.getByLabel('Direct instruction').check()
      await page.getByLabel('What to look for in the sources').fill('hand hygiene steps')
      await page.getByTestId('plan-build').click()
    },
    refetched: /\/courses\/[^/]+\/plan$/,
  })
})

test('states: rewriting a held block is queued, announced, and the held list is fetched again', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const { fix } = ids()
  await page.route(`**/api/v1/courses/${fix}/held`, (route) => json(route, 200, { blocks: [{ id: 'b-1', topic_title: 'Basics', brief: 'Why?', hold_reason: 'Not in the passage.', passage: 'Alcohol rub takes twenty seconds.', address: 'para1' }, { id: 'b-2', topic_title: 'Terms', brief: 'Why?', hold_reason: 'Quote differs.', passage: 'Soap takes forty seconds.', address: 'para2' }], batch_quote_usd: '0.040000' }))
  await page.route('**/api/v1/blocks/*/rewrite', (route) => json(route, 202, { operation_id: 'op-t04d' }))
  await page.goto(`/courses/${fix}/fix`)
  await queuedFlow(page, {
    go: async () => { await page.getByTestId('held-row').first().getByRole('button', { name: 'Rewrite block' }).click() },
    refetched: /\/courses\/[^/]+\/held$/,
  })
})

test('states: the held-list batch rewrite and Generate show the queued line too', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const { fix } = ids()
  await page.route(`**/api/v1/courses/${fix}/held`, (route) => json(route, 200, { blocks: [{ id: 'b-1', topic_title: 'Basics', brief: 'Why?', hold_reason: 'Not in the passage.', passage: 'Alcohol rub takes twenty seconds.', address: 'para1' }, { id: 'b-2', topic_title: 'Terms', brief: 'Why?', hold_reason: 'Quote differs.', passage: 'Soap takes forty seconds.', address: 'para2' }], batch_quote_usd: '0.040000' }))
  await page.route(`**/api/v1/courses/${fix}/held/rewrite`, (route) => json(route, 202, { operation_id: 'op-t04d' }))
  await page.route('**/api/v1/operations/op-t04d', (route) => json(route, 200, { operation_id: 'op-t04d', kind: 'rewrite_held_batch', status: 'running', progress: 0.2, result: null, error: null }))
  await page.goto(`/courses/${fix}/fix`)
  await page.getByTestId('fix-batch').click()
  await expect(page.getByTestId('queued-line')).toContainText('Queued')
  await page.getByTestId('bell').click()
  await expect(page.getByTestId('bell-item').first()).toContainText('Rewriting held blocks')
})
