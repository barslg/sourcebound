import { ENDPOINTS, LIVE_ENDPOINTS } from '../src/api/endpoints'
import { expect, ids, recordApi, signInAs, test } from './fixtures'

const known = new Set<string>(Object.values(ENDPOINTS))
const patterns = [
  /^\/api\/v1\/users\/[^/]+(\/ban)?$/, /^\/api\/v1\/roles\/[^/]+(\/assign|\/revoke)?$/,
  /^\/api\/v1\/courses\/[^/]+(\/(profile|plan|plan\/validate|plan\/submit|generate\/submit|sources|estimate|held|held\/rewrite|publish))?$/,
  /^\/api\/v1\/sources\/[^/]+\/(approve|reject)$/, /^\/api\/v1\/runs\/[^/]+(\/(resume|cancel))?$/, /^\/api\/v1\/blocks\/[^/]+(\/(rewrite|accept))?$/,
  /^\/api\/v1\/operations\/[^/]+$/, /^\/api\/v1\/invites\/[^/]+\/(revoke|accept)$/,
]

test('every request path comes from the endpoint table and unsafe ones carry CSRF', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const calls = recordApi(page)
  await page.goto('/users')
  await expect(page.getByTestId('users-table')).toBeVisible()
  await page.goto('/roles')
  await expect(page.getByTestId('roles-table')).toBeVisible()
  await page.getByTestId('bell').click()
  await page.getByTestId('bell-run').click()
  await expect(page.getByTestId('bell-item').first()).toBeVisible()
  await page.goto('/courses')
  await expect(page.locator('[data-view]')).toBeVisible()
  for (const path of [`/courses/${ids().course}/plan`, `/courses/${ids().course}/sources`, `/courses/${ids().course}/fix`, `/runs/${ids().run}`]) {
    await page.goto(path)
    await expect(page.locator('main h1, main h2').first()).toBeVisible()
    await page.waitForLoadState('networkidle')
  }
  expect(calls.length).toBeGreaterThan(4)
  for (const c of calls) {
    expect(known.has(c.path) || patterns.some((p) => p.test(c.path)), `unexpected path ${c.path}`).toBe(true)
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(c.method)) expect(c.csrf, `${c.method} ${c.path}`).toBeTruthy()
    if (c.method === 'GET') expect(c.csrf).toBeUndefined()
  }
  expect(LIVE_ENDPOINTS.every((e) => known.has(e))).toBe(true)
})

test('one event stream is shared by two tabs of the same browser', async ({ context }) => {
  await signInAs(context, 'admin')
  const hits: string[] = []
  context.on('request', (req) => { if (new URL(req.url()).pathname === ENDPOINTS.events) hits.push(req.url()) })
  const a = await context.newPage()
  const b = await context.newPage()
  await a.goto('/users')
  await b.goto('/roles')
  await expect(a.getByTestId('users-table')).toBeVisible()
  await expect(b.getByTestId('roles-table')).toBeVisible()
  await a.waitForTimeout(1500)
  expect(hits).toHaveLength(1)
})

test('a test operation goes queued, toast, bell, then finishes over SSE', async ({ page, context }) => {
  await signInAs(context, 'admin')
  await page.goto('/roles')
  await page.getByTestId('bell').click()
  await expect(page.getByTestId('bell-empty')).toBeVisible()
  await page.getByTestId('bell-run').click()
  await expect(page.getByText('Queued. You will find it in the bell.')).toBeVisible()
  const item = page.getByTestId('bell-item').first()
  await expect(item).toBeVisible()
  await expect(item).toContainText(/done|succeeded/i, { timeout: 30000 })
})
