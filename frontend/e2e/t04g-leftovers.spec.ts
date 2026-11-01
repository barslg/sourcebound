import type { Page, Route } from '@playwright/test'
import { expect, ids, signInAs, test } from './fixtures'

const json = (route: Route, status: number, body: unknown): Promise<void> =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

async function openDrawer(page: Page, query = ''): Promise<void> {
  await signInAs(page.context(), 'admin')
  await page.goto(`/courses/${ids().sources}/sources${query}`)
  await page.getByTestId('source-add').click()
  await expect(page.getByTestId('file-choose')).toBeVisible()
}

test('file input: a styled control in the app language, no native text, keyboard reachable, with Remove', async ({ page }) => {
  await openDrawer(page)
  const native = page.getByTestId('source-file')
  expect(await native.evaluate((el: HTMLInputElement) => ({ tab: el.tabIndex, hidden: el.getBoundingClientRect().width <= 1 }))).toEqual({ tab: -1, hidden: true })
  await expect(page.getByRole('dialog')).not.toContainText(/No file chosen/i)
  await expect(page.getByTestId('file-choose')).toHaveText(/Choose file/)
  await expect(page.getByTestId('file-name')).toHaveCount(0)
  await page.getByTestId('file-choose').focus()
  await expect(page.getByTestId('file-choose')).toBeFocused()
  const chooser = page.waitForEvent('filechooser', { timeout: 10_000 })
  await page.keyboard.press('Enter')
  await (await chooser).setFiles({ name: 'hand-hygiene.md', mimeType: 'text/markdown', buffer: Buffer.from('# Hands\n\nWash.') })
  await expect(page.getByTestId('file-name')).toHaveText('hand-hygiene.md')
  await expect(page.getByRole('dialog')).not.toContainText(/No file chosen/i)
  const remove = page.getByTestId('file-remove')
  await expect(remove).toHaveAccessibleName(/Remove file/)
  await remove.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('file-name')).toHaveCount(0)
  await expect(page.getByTestId('file-choose')).toBeVisible()
})

test('file input: the label follows the app language', async ({ page }) => {
  await openDrawer(page, '?lang=nb')
  await expect(page.getByTestId('file-choose')).toHaveText(/Velg fil/)
  await expect(page.getByRole('dialog')).not.toContainText(/No file chosen|Ingen fil/i)
})

test('row More menu: stays open for the last row of a scrolling table, even when opening it scrolls the row into view', async ({ page, context }) => {
  await signInAs(context, 'admin')
  await page.goto('/users')
  await page.waitForLoadState('networkidle')
  const more = page.getByRole('button', { name: /^More actions for/ })
  await more.last().click()
  await expect(page.getByRole('menu')).toBeVisible()
  await page.waitForTimeout(1500)
  await expect(page.getByRole('menu')).toBeVisible()
})

test('row More menu: Esc closes it and focus returns to its button', async ({ page, context }) => {
  await signInAs(context, 'admin')
  await page.goto('/users')
  await page.waitForLoadState('networkidle')
  const more = page.getByRole('button', { name: /^More actions for/ }).first()
  await more.focus()
  await page.keyboard.press('Enter')
  const menu = page.getByRole('menu')
  await expect(menu).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(menu).toBeHidden()
  await expect(more).toBeFocused()
})

test('course header shows language, audience and the topic and block counts of the course row', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const rows: Array<{ language: string; topic_count: number; block_count: number; profile: { audience: string } }> = []
  page.on('response', async (res) => {
    if (new URL(res.url()).pathname === `/api/v1/courses/${ids().course}` && res.request().method() === 'GET') rows.push(await res.json())
  })
  await page.goto(`/courses/${ids().course}/plan`)
  const facts = page.getByTestId('course-facts')
  await expect(facts).toContainText('English')
  await expect(facts).toContainText('new nurses')
  await expect(facts).toContainText('3 topics')
  await expect(facts).toContainText('6 blocks')
  expect(rows[0]).toMatchObject({ language: 'en', topic_count: 3, block_count: 6 })
})

test('translations: done, held and failed states come from the backend with the held count', async ({ page, context }) => {
  await signInAs(context, 'admin')
  await page.goto(`/courses/${ids().translated}/publish`)
  const panel = page.getByTestId('translations')
  await expect(panel.getByTestId('translation-nb')).toContainText('Held')
  await expect(panel.getByTestId('translation-nb')).toContainText('2 blocks held')
  await expect(panel.getByTestId('translation-uk')).toContainText('Ready')
  await page.goto(`/courses/${ids().translate_failed}/publish`)
  await expect(page.getByTestId('translation-uk')).toContainText('Failed')
  await page.goto(`/courses/${ids().no_version}/publish`)
  await expect(page.getByTestId('translate-open')).toBeDisabled()
  await expect(page.getByText('Publish the course first, then translate it.')).toBeVisible()
})

test('translate: POST languages, 202, Queued line, bell, then the language status is fetched again', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const { translated } = ids()
  const bodies: unknown[] = []
  let polls = 0
  await page.route(`**/api/v1/courses/${translated}/translate`, (route) => { bodies.push(route.request().postDataJSON()); return json(route, 202, { operation_id: 'op-t04g' }) })
  await page.route('**/api/v1/operations/op-t04g', (route) => { polls += 1; return json(route, 200, { operation_id: 'op-t04g', kind: 'translate', status: polls < 3 ? 'queued' : 'done', progress: polls < 3 ? 0 : 1, result: null, error: null }) })
  const refetch: string[] = []
  page.on('request', (req) => { if (req.method() === 'GET') refetch.push(new URL(req.url()).pathname) })
  await page.goto(`/courses/${translated}/publish`)
  await page.getByLabel('Norsk bokmål').check()
  await page.getByTestId('translate-open').click()
  await expect(page.getByTestId('queued-line').first()).toContainText('Queued')
  await expect(page.getByTestId('queued-line').first()).toContainText('bell')
  expect(bodies).toEqual([{ languages: ['nb'] }])
  await expect(page.getByText('Operation finished.').first()).toBeVisible({ timeout: 15000 })
  await expect(page.getByTestId('queued-line')).toHaveCount(0)
  expect(refetch.slice(refetch.findIndex((p) => p.includes('/operations/op-t04g'))).some((p) => p === `/api/v1/courses/${translated}`)).toBe(true)
})

test('preview as learner: language picker en/nb/uk, held blocks show the localized notice from the backend', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const { translated } = ids()
  const docs: Array<{ language: string; blocks: Array<{ fallback_notice: string | null; plan_node_id: string }> }> = []
  page.on('response', async (res) => {
    const url = new URL(res.url())
    if (url.pathname === `/api/v1/courses/${translated}/learner`) docs.push({ ...(await res.json()), language: url.searchParams.get('lang') ?? '' })
  })
  await page.goto(`/courses/${translated}/publish`)
  await page.getByTestId('preview-link').click()
  await expect(page).toHaveURL(new RegExp(`/courses/${translated}/preview`))
  const picker = page.getByTestId('preview-language')
  await picker.click()
  await expect(page.getByRole('option')).toHaveText(['English', 'Norsk bokmål', 'Українська'])
  await page.getByRole('option', { name: 'Norsk bokmål' }).click()
  await expect.poll(() => docs.some((d) => d.language === 'nb')).toBe(true)
  const nb = docs.find((d) => d.language === 'nb')
  const notices = (nb?.blocks ?? []).map((b) => b.fallback_notice).filter((n): n is string => n !== null)
  expect(notices.length).toBeGreaterThanOrEqual(2)
  for (const text of notices) await expect(page.getByTestId('preview-notice').filter({ hasText: text }).first()).toBeVisible()
  await expect(page.getByTestId('preview-notice')).toHaveCount(notices.length)
  await expect(page.getByTestId('preview-block').filter({ hasText: 'nb text of' }).first()).toBeVisible()
  await picker.click()
  await page.getByRole('option', { name: 'English' }).click()
  await expect(page.getByTestId('preview-notice')).toHaveCount(0)
})
