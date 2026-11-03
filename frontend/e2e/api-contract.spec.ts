import { ENDPOINTS, LIVE_ENDPOINTS } from '../src/api/endpoints'
import { expect, ids, recordApi, sessions, signInAs, test } from './fixtures'

const known = new Set<string>(Object.values(ENDPOINTS))
const patterns = [
  /^\/api\/v1\/users\/[^/]+(\/ban)?$/, /^\/api\/v1\/roles\/[^/]+(\/assign|\/revoke)?$/,
  /^\/api\/v1\/courses\/[^/]+(\/(profile|plan|plan\/validate|plan\/submit|generate\/submit|sources|estimate|held|held\/rewrite|held\/recheck|publish|runs))?$/,
  /^\/api\/v1\/sources\/[^/]+\/(approve|reject)$/, /^\/api\/v1\/runs\/[^/]+(\/(resume|cancel))?$/, /^\/api\/v1\/blocks\/[^/]+(\/(rewrite|accept))?$/,
  /^\/api\/v1\/operations\/[^/]+$/, /^\/api\/v1\/human-queue(\/[^/]+\/resolve)?$/, /^\/api\/v1\/invites\/[^/]+\/(revoke|accept)$/, /^\/api\/v1\/notifications\/[^/]+\/read$/,
]

test('every request path comes from the endpoint table and unsafe ones carry CSRF', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const calls = recordApi(page)
  await page.goto('/users')
  await expect(page.getByTestId('users-table')).toBeVisible()
  await page.goto('/roles')
  await expect(page.getByTestId('roles-table')).toBeVisible()
  await page.goto('/courses')
  await expect(page.getByTestId('course-list').or(page.locator('[data-view]'))).toBeVisible()
  for (const path of [`/courses/${ids().course}/plan`, `/courses/${ids().course}/sources`, `/courses/${ids().fix}/fix`, `/courses/${ids().fix}/publish`, `/runs/${ids().run}`]) {
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

test('a real queued operation goes queued, toast, bell, then reaches a final state over SSE', async ({ page }) => {
  await signInAs(page.context(), 'admin')
  const made = await page.request.post('/api/v1/courses', { headers: { 'X-CSRF-Token': sessions().admin.csrf }, data: { title: 'Queue lab', language: 'en', scope_tags: ['queue-lab'] } })
  const { course_id: courseId } = (await made.json()) as { course_id: string }
  const headers = { 'X-CSRF-Token': sessions().admin.csrf }
  const up = await page.request.post(`/api/v1/sources/upload?media=text&course_id=${courseId}`, { headers, multipart: { file: { name: 'hygiene.txt', mimeType: 'text/plain', buffer: Buffer.from('Wash hands for twenty seconds.\n\nUse soap and water.') } } })
  const { source_id: sourceId } = (await up.json()) as { source_id: string }
  await page.request.post(`/api/v1/sources/${sourceId}/approve`, { headers })
  await page.goto(`/courses/${courseId}/plan`)
  await page.getByLabel('Direct instruction').check()
  await page.getByLabel('What to look for in the sources').fill('hand hygiene steps')
  await page.getByTestId('plan-build').click()
  await expect(page.getByText('Queued. You will find it in the bell.').first()).toBeVisible()
  await page.getByTestId('bell').click()
  const item = page.getByTestId('bell-item').first()
  await expect(item).toBeVisible()
  await expect(item).toContainText(/Done|Failed/, { timeout: 30000 })
})
