import { expect, ids, sessions, signInAs, test } from './fixtures'
import type { Page } from '@playwright/test'

const RAW_IDENTIFIER = /\b[a-z]+_[a-z_]+\b/
const UUID = /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/i
const BACKEND = `http://127.0.0.1:${process.env.E2E_BACKEND_PORT ?? '8010'}`

type Call = `${'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'} /api/v1/${string}` | 'ui'

/** The fixture map: every enabled button of the author surface and the endpoint (or "ui") that backs it. */
const CONTROLS: Array<[RegExp, Call]> = [
  [/^Switch to (dark|light) theme$/, 'ui'],
  [/^Keyboard shortcuts$/, 'ui'],
  [/^More actions for /, 'ui'],
  [/^Sign out$/, 'POST /api/v1/auth/logout'],
  [/^Open notifications/, 'POST /api/v1/notifications/{}/read'],
  [/^Send a test task$/, 'POST /api/v1/operations/echo'],
  [/^New course$/, 'POST /api/v1/courses'],
  [/^Create your first course$/, 'POST /api/v1/courses'],
  [/^Create course$/, 'POST /api/v1/courses'],
  [/^Cancel$/, 'ui'],
  [/^Close$/, 'ui'],
  [/^Add source$/, 'ui'],
  [/^Add your first source$/, 'ui'],
  [/^Upload file$/, 'POST /api/v1/sources/upload'],
  [/^Add web address$/, 'POST /api/v1/sources/fetch'],
  [/^Approve source$/, 'POST /api/v1/sources/{}/approve'],
  [/^Reject source$/, 'POST /api/v1/sources/{}/reject'],
  [/^Approve address$/, 'POST /api/v1/human-queue/{}/resolve'],
  [/^Dismiss address$/, 'POST /api/v1/human-queue/{}/resolve'],
  [/^Save plan$/, 'PATCH /api/v1/courses/{}/plan'],
  [/^Apply new title$/, 'ui'],
  [/^Move (up|down)$/, 'ui'],
  [/^Delete from plan$/, 'ui'],
  [/^Save answer$/, 'ui'],
  [/^Generate course$/, 'POST /api/v1/courses/{}/generate/submit'],
  [/^Build plan$/, 'POST /api/v1/courses/{}/plan/submit'],
  [/^Show or hide the topics of /, 'ui'],
  [/^Rewrite all held blocks/, 'POST /api/v1/courses/{}/held/rewrite'],
  [/^Rewrite block$/, 'POST /api/v1/blocks/{}/rewrite'],
  [/^Edit brief$/, 'ui'],
  [/^Save brief$/, 'PATCH /api/v1/blocks/{}'],
  [/^Accept as checked by me$/, 'POST /api/v1/blocks/{}/accept'],
  [/^Publish course$/, 'POST /api/v1/courses/{}/publish'],
  [/^Publish$/, 'POST /api/v1/courses/{}/publish'],
  [/^Resume run$/, 'POST /api/v1/runs/{}/resume'],
  [/^Cancel run$/, 'POST /api/v1/runs/{}/cancel'],
  [/^Assign role$/, 'POST /api/v1/roles/{}/assign'],
  [/^Ban user$/, 'POST /api/v1/users/{}/ban'],
  [/^Delete user$/, 'DELETE /api/v1/users/{}'],
  [/^New role$/, 'POST /api/v1/roles'],
  [/^Edit role$/, 'PUT /api/v1/roles/{}'],
  [/^Delete role$/, 'DELETE /api/v1/roles/{}'],
  [/^Create role$/, 'POST /api/v1/roles'],
  [/^Save role$/, 'PUT /api/v1/roles/{}'],
  [/^Create invite$/, 'POST /api/v1/invites'],
  [/^Copy link$/, 'ui'],
  [/^Revoke invite$/, 'POST /api/v1/invites/{}/revoke'],
  [/^Keep editing$/, 'ui'],
  [/^Discard$/, 'ui'],
  [/^Reload theirs$/, 'GET /api/v1/courses/{}/plan'],
  [/^Keep mine$/, 'PATCH /api/v1/courses/{}/plan'],
  [/^Try again$/, 'ui'],
]

function shape(path: string): string {
  return path.replace(/\{[^}]*\}/g, '{}')
}

test('every control in the fixture map is backed by a route the backend really serves', async ({ request }) => {
  const spec = (await (await request.get(`${BACKEND}/openapi.json`)).json()) as { paths: Record<string, Record<string, unknown>> }
  const served = new Set<string>()
  for (const [path, methods] of Object.entries(spec.paths)) for (const m of Object.keys(methods)) served.add(`${m.toUpperCase()} ${shape(path)}`)
  const missing = CONTROLS.map(([, call]) => call).filter((c) => c !== 'ui' && !served.has(c))
  expect(missing).toEqual([])
})

interface Sweep {
  helpMissing: boolean
  headingsWithoutHelp: string[]
  fieldsWithoutHint: string[]
  unmapped: string[]
  disabledWithoutReason: string[]
  raw: string[]
  licenceHost: number
}

async function sweep(page: Page): Promise<Sweep> {
  const buttons = await page.evaluate(() => {
    const seen = (el: Element): boolean => (el as HTMLElement).checkVisibility()
    return [...document.querySelectorAll('button, a[role="button"]')]
      .filter((el) => seen(el) && !el.closest('.p-paginator') && !el.closest('.p-toast'))
      .map((el) => {
        const b = el as HTMLButtonElement
        const name = (b.getAttribute('aria-label') ?? b.innerText ?? '').replace(/\s+/g, ' ').trim()
        const reason = b.parentElement?.querySelector('[data-testid="reason"]')?.textContent?.trim() ?? ''
        return { name, disabled: b.disabled || b.getAttribute('aria-disabled') === 'true', reason }
      })
  })
  const rest = await page.evaluate(() => {
    const seen = (el: Element): boolean => (el as HTMLElement).checkVisibility()
    const help = document.querySelector('[data-testid="page-help"]')
    const headings = [...document.querySelectorAll('main h2, [role="dialog"] h2')]
      .filter((h) => seen(h) && !h.closest('.state'))
      .filter((h) => {
        const next = h.nextElementSibling
        return !(next && next.getAttribute('data-testid') === 'section-help' && (next.textContent ?? '').trim().length > 0)
      })
      .map((h) => (h.textContent ?? '').trim())
    const fields = [...document.querySelectorAll('.field')]
      .filter((f) => seen(f) && !f.querySelector('.hint'))
      .map((f) => (f.querySelector('label')?.textContent ?? '?').trim())
    return {
      helpMissing: !help || (help.textContent ?? '').trim().length === 0,
      headings,
      fields,
      text: document.body.innerText,
      host: document.querySelectorAll('#p-license-host, p-license-host').length,
    }
  })
  const unmapped = buttons.filter((b) => !b.disabled && !CONTROLS.some(([re]) => re.test(b.name))).map((b) => b.name)
  const noReason = buttons.filter((b) => b.disabled && b.reason.length === 0).map((b) => b.name)
  const raw = [...(rest.text.match(new RegExp(RAW_IDENTIFIER.source, 'g')) ?? []), ...(rest.text.match(new RegExp(UUID.source, 'gi')) ?? [])]
  return { helpMissing: rest.helpMissing, headingsWithoutHelp: rest.headings, fieldsWithoutHint: rest.fields, unmapped, disabledWithoutReason: noReason, raw, licenceHost: rest.host }
}

async function open(page: Page, path: string): Promise<void> {
  await page.goto(path)
  await expect(page.locator('main')).toBeVisible()
  await page.waitForLoadState('networkidle')
  await expect(page.locator('[data-view="loading"]')).toHaveCount(0)
}

function routes(): string[] {
  const i = ids()
  return [
    '/courses', '/users', '/roles', '/invites',
    `/courses/${i.course}/plan`, `/courses/${i.course}/sources`, `/courses/${i.course}/fix`, `/courses/${i.course}/publish`,
    `/courses/${i.empty}/plan`, `/courses/${i.sources}/sources`, `/courses/${i.fix}/fix`, `/courses/${i.fix}/publish`,
    `/runs/${i.run}`, `/runs/${i.live_run}`,
  ]
}

test('walk: every route of the author surface follows the labelling grammar', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const problems: string[] = []
  for (const path of routes()) {
    await open(page, path)
    const s = await sweep(page)
    const tag = (kind: string, items: string[]): void => { for (const it of items) problems.push(`${path} ${kind}: ${it}`) }
    if (s.helpMissing) problems.push(`${path} page has no one-sentence description`)
    tag('section without help line', s.headingsWithoutHelp)
    tag('field without example or help', s.fieldsWithoutHint)
    tag('enabled button not backed in the fixture map', s.unmapped)
    tag('disabled button without a visible reason', s.disabledWithoutReason)
    tag('raw identifier on screen', s.raw)
    if (s.licenceHost > 0) problems.push(`${path} shows the licence banner host`)
  }
  expect(problems).toEqual([])
})

test('walk: the public and signed-out screens follow the same grammar', async ({ browser }) => {
  const problems: string[] = []
  const anon = await browser.newContext()
  const anonPage = await anon.newPage()
  for (const path of ['/signin', `/invite/${'ab'.repeat(32)}`]) {
    await anonPage.goto(path)
    await anonPage.waitForLoadState('networkidle')
    const s = await sweep(anonPage)
    if (s.helpMissing) problems.push(`${path} page has no one-sentence description`)
    for (const r of s.raw) problems.push(`${path} raw identifier: ${r}`)
    for (const b of s.unmapped) problems.push(`${path} unmapped button: ${b}`)
    if (s.licenceHost > 0) problems.push(`${path} shows the licence banner host`)
  }
  await anon.close()
  const waiting = await browser.newContext()
  await signInAs(waiting, 'waiting')
  const wp = await waiting.newPage()
  await wp.goto('/waiting')
  await wp.waitForLoadState('networkidle')
  const w = await sweep(wp)
  if (w.helpMissing) problems.push('/waiting page has no one-sentence description')
  for (const b of w.unmapped) if (!/^Check again$/.test(b)) problems.push(`/waiting unmapped button: ${b}`)
  await waiting.close()
  expect(problems).toEqual([])
})

test('walk: dialogs follow the grammar too', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const problems: string[] = []
  const check = async (where: string): Promise<void> => {
    const s = await sweep(page)
    for (const f of s.fieldsWithoutHint) problems.push(`${where} field without help: ${f}`)
    for (const b of s.disabledWithoutReason) problems.push(`${where} disabled without reason: ${b}`)
    for (const b of s.unmapped) problems.push(`${where} unmapped: ${b}`)
    for (const r of s.raw) problems.push(`${where} raw identifier: ${r}`)
  }
  await open(page, '/courses')
  await page.getByTestId('course-new').click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await check('new course dialog')
  await page.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click()
  await open(page, '/roles')
  await page.getByTestId('role-new').click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await check('new role dialog')
  await page.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click()
  await open(page, '/users')
  const row = page.getByRole('row').filter({ hasText: sessions().student.email })
  for (const name of ['Assign role', 'Ban user', 'Delete user']) {
    if (name === 'Assign role') await row.getByRole('button', { name }).click()
    else {
      await row.getByRole('button', { name: /^More actions for/ }).click()
      await page.getByRole('menuitem', { name }).click()
    }
    await expect(page.getByRole('dialog')).toBeVisible()
    await check(`${name} dialog`)
    await page.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click()
  }
  await open(page, `/courses/${ids().publish}/publish`)
  await page.getByTestId('publish-open').click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await check('publish dialog')
  expect(problems).toEqual([])
})

test('walk: the administrator role cannot be edited or deleted, so those controls say why instead of failing', async ({ page, context }) => {
  await signInAs(context, 'admin')
  await open(page, '/roles')
  const row = page.getByRole('row').filter({ hasText: 'administrator' })
  await expect(row.getByRole('button', { name: 'Edit role' })).toBeDisabled()
  await expect(row.getByTestId('reason').first()).toContainText('fixed')
  await row.getByRole('button', { name: /^More actions for/ }).click()
  await expect(page.getByRole('menuitem', { name: /Delete role/ })).toHaveAttribute('aria-disabled', 'true')
  await expect(page.getByRole('menuitem', { name: /Delete role/ }).getByTestId('reason')).toContainText('fixed')
})

test('walk: every "Needs now" line links to a control that exists on the target page', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const i = ids()
  await open(page, '/courses')
  const hrefs = await page.getByTestId('needs-link').evaluateAll((els) => els.map((e) => (e as HTMLAnchorElement).getAttribute('href') ?? ''))
  expect(hrefs.length).toBeGreaterThan(3)
  for (const tab of [`/courses/${i.course}/plan`, `/courses/${i.sources}/sources`, `/courses/${i.conflict}/plan`, `/courses/${i.empty}/plan`]) {
    await open(page, tab)
    const link = page.getByTestId('course-head').getByTestId('needs-link')
    await expect(link, tab).toHaveCount(1)
    hrefs.push((await link.getAttribute('href')) ?? '')
  }
  for (const href of new Set(hrefs)) {
    const [path, hash] = href.split('#')
    expect(hash, `${href} has no target`).toBeTruthy()
    await open(page, path as string)
    await expect(page.locator(`#${hash}`), `${href} resolves`).toHaveCount(1)
  }
})

test('walk: tab counts equal the API counts', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const i = ids()
  for (const id of [i.course, i.sources, i.fix, i.empty]) {
    const sources = (await (await page.request.get(`/api/v1/courses/${id}/sources`)).json()) as Array<{ approval: string }>
    const held = (await (await page.request.get(`/api/v1/courses/${id}/held`)).json()) as { blocks: unknown[] }
    await open(page, `/courses/${id}/plan`)
    const sourcesTab = page.getByTestId('tab-sources')
    const fixTab = page.getByTestId('tab-fix')
    if (sources.length === 0) await expect(sourcesTab).toContainText('0')
    else {
      await expect(sourcesTab).toContainText(String(sources.length))
      const pending = sources.filter((s) => s.approval === 'pending').length
      if (pending > 0) await expect(sourcesTab).toContainText(`${pending} pending`)
      else await expect(sourcesTab).not.toContainText('pending')
    }
    await expect(fixTab).toContainText(String(held.blocks.length))
  }
})

test('licence: the bundle holds no licence-manager code and the page no licence host', async ({ page, context, request }) => {
  await signInAs(context, 'admin')
  const html = await (await request.get(`http://127.0.0.1:${process.env.E2E_PREVIEW_PORT ?? '4173'}/`)).text()
  const scripts = [...html.matchAll(/src="([^"]+\.js)"/g)].map((m) => m[1] as string)
  expect(scripts.length).toBeGreaterThan(0)
  const bundle: string[] = []
  const queue = [...scripts]
  const seen = new Set<string>()
  while (queue.length > 0) {
    const url = queue.pop() as string
    if (seen.has(url)) continue
    seen.add(url)
    const body = await (await request.get(new URL(url, `http://127.0.0.1:${process.env.E2E_PREVIEW_PORT ?? '4173'}/`).toString())).text()
    bundle.push(body)
    for (const m of body.matchAll(/["'`]\.\/([\w.-]+\.js)["'`]/g)) queue.push(`/assets/${m[1]}`)
  }
  const all = bundle.join('\n')
  expect(all).not.toMatch(/license-manager|PrimeUI License|p-license-host/i)
  await open(page, '/courses')
  await expect(page.locator('#p-license-host')).toHaveCount(0)
})
