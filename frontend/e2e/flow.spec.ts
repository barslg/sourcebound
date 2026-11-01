import { expect, signInAs, test, type Who } from './fixtures'
import type { APIRequestContext, BrowserContext } from '@playwright/test'

// The UI's live-shaped acceptance: the author path on the REAL API and worker, with the model
// replaced by the mock model server (e2e/tools/mock_model.py). Screens AND the API state behind them.
test.describe.configure({ mode: 'serial', timeout: Number(process.env.FLOW_TIMEOUT_MS ?? 600_000) })

let courseId = ''
let inviteUrl = ''
const TITLE = `Flow course ${Date.now()}`
const PREVIEW = process.env.E2E_PREVIEW_PORT ?? '4173'

async function api<T>(context: BrowserContext, method: 'GET' | 'POST' | 'PATCH', path: string, body?: unknown): Promise<T> {
  const request: APIRequestContext = context.request
  const base = `http://127.0.0.1:${PREVIEW}/api/v1`
  const headers: Record<string, string> = {}
  if (method !== 'GET') headers['X-CSRF-Token'] = ((await (await request.get(`${base}/auth/csrf`)).json()) as { csrf_token: string }).csrf_token
  const res = await request.fetch(`${base}${path}`, { method, headers, data: body })
  expect(res.ok(), `${method} ${path} -> ${res.status()}`).toBe(true)
  return (await res.json()) as T
}

async function until<T>(read: () => Promise<T>, done: (v: T) => boolean, what: string, ms = 180_000): Promise<T> {
  const end = Date.now() + ms
  for (;;) {
    const v = await read()
    if (done(v)) return v
    if (Date.now() > end) throw new Error(`timed out: ${what}`)
    await new Promise((r) => setTimeout(r, 2000))
  }
}

interface CourseApi { status: string; held_count: number; draft_count: number; source_count: number; current_version_id: string | null; translations: Array<{ language: string; status: string }> }
interface PlanApi { modules: Array<{ topics: Array<{ blocks: Array<{ id: string; status: string }> }> }> }
const blocksOf = (p: PlanApi) => p.modules.flatMap((m) => m.topics.flatMap((t) => t.blocks))

test('author path: create, source, plan, generate, fix one block, translate, preview, publish, invite, student lands', async ({ browser }) => {
  const admin = await browser.newContext({ locale: 'en-US' })
  await signInAs(admin, 'admin' as Who)
  const page = await admin.newPage()

  await page.goto('/courses')
  await page.getByTestId('course-new').click()
  await page.locator('#c-title').fill(TITLE)
  await page.getByTestId('dialog-submit').click()
  await page.waitForURL(/\/courses\/[0-9a-f-]+\/plan/)
  courseId = /courses\/([0-9a-f-]+)\//.exec(page.url())?.[1] ?? ''
  expect(courseId).not.toBe('')

  await page.goto(`/courses/${courseId}/sources`)
  await page.getByTestId('source-add').first().click()
  await page.getByLabel('Licence').fill('CC BY 4.0')
  await page.getByLabel('As of').fill('2026-10-02')
  await page.getByTestId('source-file').setInputFiles({ name: 'hygiene.txt', mimeType: 'text/plain', buffer: Buffer.from('Wash hands for at least twenty seconds with soap and water.\n\nAlcohol rub is used when hands are not visibly soiled.\n\nHands are cleaned before touching a patient and after touching surroundings.') })
  await page.getByTestId('source-upload').click()
  await expect(page.getByText('Source uploaded.')).toBeVisible()
  await page.getByRole('button', { name: /^Approve source$/ }).first().click()
  const afterApprove = await until(() => api<CourseApi>(admin, 'GET', `/courses/${courseId}`), (c) => c.source_count > 0, 'source row')
  expect(afterApprove.source_count).toBe(1)

  await page.goto(`/courses/${courseId}/plan`)
  await page.locator('label[for^="m-"]').first().click()
  await page.locator('#b-aud').fill('ward nurses')
  await page.locator('#b-q').fill('hand hygiene')
  await page.getByTestId('plan-build').click()
  await expect(page.getByTestId('plan-tree')).toBeVisible({ timeout: 180_000 })
  const planned = await api<PlanApi>(admin, 'GET', `/courses/${courseId}/plan`)
  expect(blocksOf(planned).length).toBeGreaterThan(0)

  await page.reload()
  await page.getByTestId('generate').click()
  const run = await until(() => api<Array<{ id: string; status: string; kind: string | null }>>(admin, 'GET', `/courses/${courseId}/runs`), (r) => r.some((x) => x.kind === 'generation' && x.status === 'done'), 'generation run done')
  const gen = run.find((x) => x.kind === 'generation')
  const generated = await api<CourseApi>(admin, 'GET', `/courses/${courseId}`)
  expect(generated.held_count).toBeGreaterThan(0)
  expect(generated.draft_count).toBe(0)
  console.log(`FLOW generation run ${gen?.id} status ${gen?.status} held ${generated.held_count} draft ${generated.draft_count}`)

  const squash = (v: string): string => v.replace(/\s+/g, ' ').trim()
  await page.goto('/courses')
  const row = page.getByTestId('course-item').filter({ hasText: TITLE })
  await expect(row.getByTestId('held-count')).toBeVisible()
  const listSays = squash(await row.getByTestId('needs-now').innerText())
  await page.goto(`/courses/${courseId}/fix`)
  await expect(page.getByTestId('held-row')).toHaveCount(generated.held_count)
  expect(squash(await page.getByTestId('course-head').getByTestId('needs-now').innerText())).toBe(listSays)

  const before = blocksOf(await api<PlanApi>(admin, 'GET', `/courses/${courseId}/plan`))
  await page.getByRole('button', { name: 'Rewrite block' }).first().click()
  await until(() => api<CourseApi>(admin, 'GET', `/courses/${courseId}`), (c) => c.held_count === generated.held_count - 1, 'one block fixed')
  const after = blocksOf(await api<PlanApi>(admin, 'GET', `/courses/${courseId}/plan`))
  const changed = after.filter((b) => before.find((x) => x.id === b.id)?.status !== b.status)
  expect(changed.length).toBe(1)

  for (;;) {
    const now = await api<CourseApi>(admin, 'GET', `/courses/${courseId}`)
    if (now.held_count === 0) break
    await page.reload()
    await page.getByRole('button', { name: 'Rewrite block' }).first().click()
    await until(() => api<CourseApi>(admin, 'GET', `/courses/${courseId}`), (c) => c.held_count === now.held_count - 1, 'next block fixed')
  }
  const fixed = await until(() => api<CourseApi>(admin, 'GET', `/courses/${courseId}`), (c) => c.status === 'generated', 'course generated after the fixes')
  expect(fixed.held_count).toBe(0)
  console.log(`FLOW course ${courseId} status ${fixed.status} held ${fixed.held_count} draft ${fixed.draft_count}`)

  await page.goto(`/courses/${courseId}/publish`)
  await page.locator('#t-nb').check()
  await page.getByTestId('translate-open').click()
  await until(() => api<CourseApi>(admin, 'GET', `/courses/${courseId}`), (c) => c.translations.some((t) => t.language === 'nb' && t.status === 'ready'), 'nb translation')
  await page.goto(`/courses/${courseId}/preview?language=nb`)
  await expect(page.getByTestId('preview-block').first()).toContainText('norsk tekst')

  await page.goto(`/courses/${courseId}/publish`)
  await page.getByTestId('publish-open').click()
  await expect(page.getByTestId('publish-translations')).toContainText('nb')
  await page.getByTestId('dialog-submit').click()
  await expect(page.getByTestId('published-note')).toBeVisible()
  const published = await api<CourseApi>(admin, 'GET', `/courses/${courseId}`)
  expect(published.status).toBe('published')
  const kept = await api<{ available_languages: Array<{ language: string; status: string }>; blocks: Array<{ language: string; fallback_notice: string | null }> }>(admin, 'GET', `/courses/${courseId}/learner?lang=nb`)
  expect(kept.available_languages.some((l) => l.language === 'nb' && l.status === 'ready')).toBe(true)
  expect(kept.blocks.every((b) => b.language === 'nb' && b.fallback_notice === null)).toBe(true)

  await page.goto(`/invites?course=${courseId}`)
  await page.getByTestId('invite-create').click()
  inviteUrl = (await page.getByTestId('invite-link').first().innerText()).trim()
  expect(inviteUrl).toContain('/invite/')

  const student = await browser.newContext({ locale: 'en-US' })
  await signInAs(student, 'inviteme')
  const sp = await student.newPage()
  await sp.goto(new URL(inviteUrl).pathname)
  await expect(sp.locator('[data-view="accepted"]')).toBeVisible()
  await sp.getByTestId('invite-open').click()
  await expect(sp).toHaveURL(/\/learn$/)
  await expect(sp.getByTestId('learn-item').filter({ hasText: TITLE })).toBeVisible()
  const mine = await api<Array<{ id: string }>>(student, 'GET', '/learn/courses')
  expect(mine.some((c) => c.id === courseId)).toBe(true)
  await sp.screenshot({ path: '../tmp/shots-t04i/student-landing.png' })
})
