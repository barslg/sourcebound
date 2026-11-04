import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, ids, signInAs, test, type Who } from './fixtures'
import type { APIRequestContext, BrowserContext, Locator, Page } from '@playwright/test'

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

async function until<T>(read: () => Promise<T>, done: (v: T) => boolean, what: string, ms = 180_000, step = 2000): Promise<T> {
  const end = Date.now() + ms
  for (;;) {
    const v = await read()
    if (done(v)) return v
    if (Date.now() > end) throw new Error(`timed out: ${what}`)
    await new Promise((r) => setTimeout(r, step))
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
  await page.locator('#b-q').fill('hand hygiene')
  await page.getByTestId('plan-build').click()
  await expect(page.getByTestId('plan-outline')).toBeVisible({ timeout: 180_000 })
  const planned = await api<PlanApi>(admin, 'GET', `/courses/${courseId}/plan`)
  expect(blocksOf(planned).length).toBeGreaterThan(0)

  await page.goto(`/courses/${courseId}/generate`)
  await page.getByTestId('generate').click()
  type RunRow = { id: string; status: string; kind: string | null }
  const runsOf = (): Promise<RunRow[]> => api<RunRow[]>(admin, 'GET', `/courses/${courseId}/runs`)
  // Stop the run mid-flight from the API (tight poll: the mock model is
  // fast), then resume it from the page: the run section must name its
  // steps, offer Resume, and end in the done card.
  const going = await until(runsOf, (r) => r.some((x) => x.kind === 'generation' && (x.status === 'running' || x.status === 'queued')), 'generation run active', 180_000, 500)
  const goingId = going.find((x) => x.kind === 'generation')?.id ?? ''
  expect(goingId).not.toBe('')
  type RunDocApi = { status: string; steps: Array<{ status: string }> }
  await until(() => api<RunDocApi>(admin, 'GET', `/runs/${goingId}`), (d) => d.status === 'running' && d.steps.length >= 2, 'generation run underway', 180_000, 500)
  await api<{ ok: boolean }>(admin, 'POST', `/runs/${goingId}/cancel`)
  await until(runsOf, (r) => r.some((x) => x.kind === 'generation' && x.status === 'stopped'), 'generation run stopped')
  await expect(page.getByTestId('run-stopped')).toBeVisible({ timeout: 60_000 })
  await expect(page.getByTestId('run-step').first()).toContainText('Step ')
  const resume = page.getByTestId('run-resume')
  await expect(resume).toBeEnabled({ timeout: 60_000 })
  await resume.click()
  await expect(page.getByText('Run resumed.')).toBeVisible()
  const run = await until(runsOf, (r) => r.some((x) => x.kind === 'generation' && x.status === 'done'), 'generation run done')
  const gen = run.find((x) => x.kind === 'generation')
  const generated = await api<CourseApi>(admin, 'GET', `/courses/${courseId}`)
  expect(generated.held_count).toBeGreaterThan(0)
  expect(generated.draft_count).toBe(0)
  console.log(`FLOW generation run ${gen?.id} status ${gen?.status} held ${generated.held_count} draft ${generated.draft_count}`)

  await page.reload()
  const steps = page.getByTestId('run-step')
  await expect(steps.first()).toBeVisible({ timeout: 60_000 })
  expect(await steps.count()).toBeGreaterThan(3)
  await expect(steps.first()).toContainText('Step 1:')
  await expect(steps.filter({ hasText: 'Reader check' }).first()).toContainText('When to wash')
  await expect(page.getByTestId('generate-done')).toBeVisible()
  await expect(page.getByTestId('go-fix')).toBeVisible()
  await expect(page.getByTestId('go-publish')).toBeVisible()

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
  await page.getByTestId('topic-open').first().click()
  await expect(page).toHaveURL(/\/preview\/topics\//)
  await expect(page.getByTestId('learn-block').first()).toContainText('norsk tekst')

  await page.goto(`/courses/${courseId}/publish`)
  await page.getByTestId('publish-open').click()
  await expect(page.getByTestId('publish-translations')).toContainText('nb')
  await page.getByTestId('dialog-submit').click()
  await expect(page.getByTestId('published-note')).toBeVisible()
  const published = await api<CourseApi>(admin, 'GET', `/courses/${courseId}`)
  expect(published.status).toBe('published')
  const kept = await api<{ available_languages: Array<{ language: string; status: string }>; modules: Array<{ topics: Array<{ id: string }> }> }>(admin, 'GET', `/courses/${courseId}/preview?lang=nb`)
  expect(kept.available_languages.some((l) => l.language === 'nb' && l.status === 'ready')).toBe(true)
  for (const topic of kept.modules.flatMap((m) => m.topics)) {
    const served = await api<{ blocks: Array<{ language: string; fallback_notice: string | null }> }>(admin, 'GET', `/preview/topics/${topic.id}?lang=nb`)
    expect(served.blocks.every((b) => b.language === 'nb' && b.fallback_notice === null)).toBe(true)
  }

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
  await sp.getByTestId('learn-item').filter({ hasText: TITLE }).getByTestId('learn-open').click()
  await expect(sp).toHaveURL(/\/learn\/courses\//)
  await expect(sp.getByTestId('topic-row').first()).toBeVisible()
  await expect(sp.getByTestId('learn-language')).toBeVisible()
  await sp.getByTestId('continue').click()
  await expect(sp.getByTestId('learn-block').first()).toBeVisible()
  console.log(`FLOW learner opened the generated course: ${await sp.getByTestId('learn-block').count()} blocks served`)
})

interface Chip { handle: string; address_label: string; source_title: string; passage_id: string }
interface TopicApi { blocks: Array<{ id: string; block_type: string; citations: Chip[]; attempts: { count: number }; content: Record<string, unknown> }>; progress: { status: string } }
interface AnswerApi { correct: boolean; partial: boolean; score: number; attempt_no: number; counters?: Record<string, unknown>; feedback: Record<string, unknown> }

const MOCK_LOG = join(dirname(fileURLToPath(import.meta.url)), '../../tmp/mock-model.log')
const modelCalls = (): number => (existsSync(MOCK_LOG) ? readFileSync(MOCK_LOG, 'utf8').split('\n').filter((l) => l.includes('"call"')).length : 0)

async function pick(page: Page, trigger: Locator, option: string): Promise<void> {
  await trigger.click()
  await page.getByRole('option', { name: option, exact: true }).click()
  await expect(page.getByRole('listbox')).toHaveCount(0)
}

test('learner path: enrolled student reads a topic, answers blocks, finds the error, takes the module test, asks the tutor with no model call', async ({ browser }) => {
  test.setTimeout(240_000)
  const lab = ids()
  const student = await browser.newContext({ locale: 'en-US' })
  await signInAs(student, 'student')
  const page = await student.newPage()
  const answers: Array<{ url: string; body: AnswerApi }> = []
  page.on('response', (res) => { if (/\/learn\/blocks\/[^/]+\/answer$/.test(res.url()) && res.request().method() === 'POST') void res.json().then((body: AnswerApi) => answers.push({ url: res.url(), body })) })
  const card = (type: string): Locator => page.locator(`[data-testid="learn-block"][data-type="${type}"]`).first()
  const lastAnswer = async (since: number): Promise<AnswerApi> => { await expect.poll(() => answers.length).toBeGreaterThan(since); return (answers[answers.length - 1] as { body: AnswerApi }).body }

  await page.goto('/learn')
  await page.getByTestId('learn-item').filter({ hasText: 'Learner lab' }).getByTestId('learn-open').click()
  await expect(page).toHaveURL(new RegExp(`/learn/courses/${lab.learn}$`))
  await expect(page.getByTestId('topic-row')).toHaveCount(2)
  await expect(page.getByTestId('topic-row').nth(1)).toContainText('Module test')
  await page.screenshot({ path: '../tmp/shots-t06/outline-1280.png' })
  console.log('LEARNER outline: 2 topics, module test marked, status', await page.getByTestId('course-progress').innerText())

  await page.getByTestId('continue').click()
  await expect(page).toHaveURL(new RegExp(`/learn/topics/${lab.learn_topic}`))
  await expect(page.getByTestId('learn-block')).toHaveCount(9)
  const topicApi = (await (await page.request.get(`/api/v1/learn/topics/${lab.learn_topic}`)).json()) as TopicApi
  const blockOf = (type: string): TopicApi['blocks'][number] => topicApi.blocks.find((b) => b.block_type === type) as TopicApi['blocks'][number]
  expect(JSON.stringify(topicApi)).not.toMatch(/answer_key|planted|SENTINEL/)

  // a citation mark in the text opens the companion panel beside it (no modal) and shows the passage
  await expect(page.getByTestId('companion-empty')).toBeVisible()
  await card('reading').locator('button.cite-mark').first().click()
  await expect(page.getByTestId('passage-text')).toContainText('Alcohol rub takes twenty seconds')
  await expect(page.locator('[role="dialog"]')).toHaveCount(0)
  await page.screenshot({ path: '../tmp/shots-t06/topic-1280.png', fullPage: true })
  console.log('LEARNER reading chip opened passage:', (await page.getByTestId('passage-text').innerText()).slice(0, 48))

  // choice: a wrong attempt, then the right one (attempts never lock)
  await expect(card('choice').getByTestId('check-answer')).toBeDisabled()
  await expect(card('choice').getByTestId('reason')).toContainText('Answer the block first')
  await card('choice').getByLabel('Forty seconds').check()
  await card('choice').getByTestId('check-answer').click()
  await expect(card('choice').getByTestId('result-chip')).toContainText('Not yet')
  await card('choice').getByLabel('Twenty seconds').check()
  const before2 = answers.length
  await card('choice').getByTestId('check-answer').click()
  await expect(card('choice').getByTestId('result-chip')).toHaveText(/Correct/)
  const second = await lastAnswer(before2)
  expect(second.correct).toBe(true)
  expect(second.attempt_no).toBe(2)
  expect(JSON.stringify(second.feedback)).not.toMatch(/Twenty seconds/)
  console.log(`LEARNER choice attempt ${second.attempt_no} correct ${second.correct}; feedback keys ${Object.keys(second.feedback).join(',')}`)

  // cloze
  await card('cloze').getByTestId('cloze-blank').nth(0).fill('hand')
  await card('cloze').getByTestId('cloze-blank').nth(1).fill('hygiene')
  await card('cloze').getByTestId('check-answer').click()
  await expect(card('cloze').getByTestId('result-chip')).toHaveText(/Correct/)

  // ordering: served shuffled; move the steps into the source's order, check, correct
  const steps = card('ordering').locator('[data-step]')
  const authored = ['Wet the hands', 'Rub for twenty seconds', 'Dry the hands']
  expect(await steps.locator('.ordering-text').allTextContents()).not.toEqual(authored)
  for (let want = 0; want < authored.length; want += 1) {
    let at = (await steps.locator('.ordering-text').allTextContents()).indexOf(authored[want] as string)
    for (; at > want; at -= 1) await steps.nth(at).getByTestId('step-up').click()
  }
  await card('ordering').getByTestId('check-answer').click()
  await expect(card('ordering').getByTestId('result-chip')).toHaveText(/Correct/)
  // matching: ids as served, right column as served
  const rights = card('matching').getByTestId('match-select')
  await pick(page, rights.nth(0), 'twenty seconds')
  await pick(page, rights.nth(1), 'forty seconds')
  await card('matching').getByTestId('check-answer').click()
  await expect(card('matching').getByTestId('result-chip')).toHaveText(/Correct/)

  // scenario: tiers are the frontend's fixed set, the source is picked from the block's own citations
  const label = (h: string): string => { const c = blockOf('scenario_practice').citations.find((x) => x.handle === h) as Chip; return `${c.source_title} ${c.address_label}` }
  await pick(page, card('scenario_practice').getByTestId('tier-select').nth(0), 'High risk')
  await pick(page, card('scenario_practice').getByTestId('source-select').nth(0), label('P1'))
  await pick(page, card('scenario_practice').getByTestId('tier-select').nth(1), 'Minimal risk')
  await pick(page, card('scenario_practice').getByTestId('source-select').nth(1), label('P2'))
  await card('scenario_practice').getByTestId('check-answer').click()
  await expect(card('scenario_practice').getByTestId('result-chip')).toHaveText(/Correct/)

  // find the error: sentence, kind, passage, fix; feedback reveals the sentence and the true statement; counters per kind
  const spot = card('spot_the_error')
  await spot.getByTestId('spot-sentence').nth(0).check()
  await pick(page, spot.getByTestId('spot-kind'), 'Number or date')
  const p1 = blockOf('reading').citations[0] as Chip
  await spot.getByTestId('spot-passage').locator('xpath=ancestor::label').filter({ hasText: p1.address_label }).first().locator('input').check()
  await spot.getByTestId('spot-fix').fill('Alcohol rub takes twenty seconds when hands are not visibly soiled.')
  const before3 = answers.length
  await spot.getByTestId('check-answer').click()
  await expect(spot.getByTestId('spot-wrong')).toContainText('forty seconds')
  await expect(spot.getByTestId('spot-true')).toContainText('twenty seconds')
  await expect(spot.getByTestId('spot-counters')).toContainText('Number or date')
  const found = await lastAnswer(before3)
  expect(found.correct, JSON.stringify(found)).toBe(true)
  const fold = spot.getByTestId(/^disclosure-sources-/)
  if ((await fold.count()) > 0) await fold.click()
  await spot.locator('[data-testid="cite-chip"][aria-expanded="false"]').first().click()
  await expect(page.getByTestId('passage-text')).toBeVisible()
  await page.screenshot({ path: '../tmp/shots-t06/spot-1280.png', fullPage: true })
  console.log(`LEARNER find-the-error score ${found.score}; counters ${JSON.stringify(found.counters)}`)

  const after = (await (await page.request.get(`/api/v1/learn/topics/${lab.learn_topic}`)).json()) as TopicApi
  expect(after.blocks.find((b) => b.block_type === 'choice')?.attempts.count).toBe(2)

  // the nb translation: two blocks translated, the rest show the source text with a notice
  await page.goto(`/learn/topics/${lab.learn_topic}?language=nb`)
  await expect(card('reading')).toContainText('Hendene gnis med alkohol')
  await expect(page.getByTestId('fallback-notice').first()).toBeVisible()
  console.log('LEARNER nb: translated reading shown, fallback notices', await page.getByTestId('fallback-notice').count())
  await page.goto(`/learn/topics/${lab.learn_topic}`)

  // module test: one page, submit disabled with its reason until something is answered, retake clears
  await page.goto(`/learn/topics/${lab.learn_test}`)
  await expect(page.getByTestId('learn-block')).toHaveCount(3)
  await expect(page.getByTestId('check-answer')).toHaveCount(0)
  await expect(page.getByTestId('submit-test')).toBeDisabled()
  await expect(page.getByText('Answer at least one question first.')).toBeVisible()
  await card('choice').getByLabel('Nothing, hand hygiene stays').check()
  const mt = card('matching').getByTestId('match-select')
  await pick(page, mt.nth(0), 'twenty seconds')
  await pick(page, mt.nth(1), 'forty seconds')
  await page.screenshot({ path: '../tmp/shots-t06/module-test-1280.png', fullPage: true })
  const submitted = page.waitForResponse((r) => r.url().endsWith(`/learn/topics/${lab.learn_test}/submit`))
  await page.getByTestId('submit-test').click()
  const result = (await (await submitted).json()) as { score: number; unanswered: number; results: unknown[]; mastery: { status: string } }
  expect(result.unanswered).toBe(1)
  expect(result.results).toHaveLength(2)
  await expect(page.getByTestId('test-summary')).toContainText('Test score')
  await expect(page.getByTestId('answer-result')).toHaveCount(2)
  console.log(`LEARNER module test score ${result.score} unanswered ${result.unanswered} mastery ${result.mastery.status}`)
  await page.getByTestId('retake').click()
  await expect(page.getByTestId('answer-result')).toHaveCount(0)
  await expect(page.getByTestId('submit-test')).toBeDisabled()

  // tutor: collapsed by default; an off-topic question is "not in the materials" and costs no model call
  await expect(page.getByTestId('tutor-body')).toBeHidden()
  await page.getByTestId('tutor-toggle').click()
  const calls = modelCalls()
  await page.getByTestId('tutor-question').fill('What is the airspeed velocity of an unladen swallow?')
  await page.getByTestId('tutor-ask').click()
  await expect(page.getByTestId('tutor-none')).toContainText('not in the course materials')
  expect(modelCalls()).toBe(calls)
  await page.screenshot({ path: '../tmp/shots-t06/tutor-1280.png', fullPage: true })
  console.log(`LEARNER tutor: not in the materials, model calls before ${calls} after ${modelCalls()}`)

  // the teacher report sees the learner's work
  const admin = await browser.newContext({ locale: 'en-US' })
  await signInAs(admin, 'admin')
  const ap = await admin.newPage()
  await ap.goto(`/courses/${lab.learn}/report`)
  const row = ap.getByTestId('report-learner').filter({ hasText: 'student@example.com' })
  await expect(row).toBeVisible()
  await expect(row.getByTestId('report-kinds')).toContainText('Number or date')
  const csv = await ap.request.get(`/api/v1/courses/${lab.learn}/report?format=csv`)
  expect(csv.headers()['content-type']).toContain('text/csv')
  console.log('LEARNER report: student row with find-the-error kinds, csv', csv.status())
  await ap.screenshot({ path: '../tmp/shots-t06/report-1280.png', fullPage: true })
})

test('public course: a signed-in user with no invite finds the open course in /learn and reads it', async ({ browser }) => {
  const lab = ids()
  const owner = await browser.newContext({ locale: 'en-US' })
  await signInAs(owner, 'admin')
  const visitor = await browser.newContext({ locale: 'en-US' })
  await signInAs(visitor, 'visitor')
  const page = await visitor.newPage()
  expect((await api<Array<{ id: string }>>(visitor, 'GET', '/learn/courses')).some((c) => c.id === lab.learn)).toBe(false)
  await api(owner, 'PATCH', `/courses/${lab.learn}/visibility`, { public: true })
  try {
    await page.goto('/learn')
    const item = page.getByTestId('learn-item').filter({ hasText: 'Learner lab' })
    await expect(item.getByTestId('learn-public')).toBeVisible()
    await item.getByTestId('learn-open').click()
    await expect(page).toHaveURL(new RegExp(`/learn/courses/${lab.learn}$`))
    await page.goto(`/learn/topics/${lab.learn_topic}`)
    await expect(page.getByTestId('learn-block').first()).toBeVisible()
  } finally {
    await api(owner, 'PATCH', `/courses/${lab.learn}/visibility`, { public: false })
  }
  expect((await api<Array<{ id: string }>>(visitor, 'GET', '/learn/courses')).some((c) => c.id === lab.learn)).toBe(false)
})
