import type { Page } from '@playwright/test'
import { expect, ids, signInAs, test } from './fixtures'

// Permanent gates for the four spec mutants that survived T04i. Each one reads a rule of docs/ui/GRAMMAR.md on real screens;
// break the code the way the mutant did and the matching test goes red.
function screens(): string[] {
  const i = ids()
  return [
    '/courses', '/users', '/roles', '/invites', '/learn',
    `/courses/${i.course}/plan`, `/courses/${i.course}/sources`, `/courses/${i.course}/fix`, `/courses/${i.course}/publish`, `/courses/${i.course}/runs`,
    `/courses/${i.empty}/plan`, `/courses/${i.sources}/sources`, `/courses/${i.fix}/fix`, `/courses/${i.translated}/preview`,
    `/runs/${i.run}`,
  ]
}

async function open(page: Page, path: string): Promise<void> {
  await page.goto(path)
  await expect(page.locator('main')).toBeVisible()
  await page.waitForLoadState('networkidle')
  await expect(page.locator('[data-view="loading"]')).toHaveCount(0)
}

test('gate: a clean screen offers no enabled Save (nothing to save is a disabled control with its reason)', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const enabled: string[] = []
  for (const path of screens()) {
    await open(page, path)
    const names = await page.locator('main [data-action="save"]:not([disabled])').evaluateAll((els) => els.map((e) => (e.textContent ?? '').trim() || e.getAttribute('aria-label') || '?'))
    for (const n of names) enabled.push(`${path}: ${n}`)
  }
  expect(enabled).toEqual([])
})

test('gate: every enabled icon-only button shows a tooltip equal to its label', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const problems: string[] = []
  let checked = 0
  for (const path of screens()) {
    await open(page, path)
    const buttons = page.locator('main button[data-action][aria-label]:not([disabled])')
    const count = Math.min(await buttons.count(), 3)
    for (let n = 0; n < count; n += 1) {
      const button = buttons.nth(n)
      const label = (await button.getAttribute('aria-label')) ?? ''
      await button.hover()
      checked += 1
      const tip = page.locator('.p-tooltip').filter({ hasText: label }).first()
      if (!(await tip.waitFor({ state: 'visible', timeout: 2500 }).then(() => true, () => false))) problems.push(`${path}: no tooltip on "${label}"`)
      await page.mouse.move(0, 0)
    }
  }
  expect(checked).toBeGreaterThan(5)
  expect(problems).toEqual([])
})

test('gate: every select and multi-select on a screen has an accessible name', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const nameless: string[] = []
  let checked = 0
  for (const path of screens()) {
    await open(page, path)
    const boxes = page.locator('main [role="combobox"]:visible')
    const count = await boxes.count()
    for (let n = 0; n < count; n += 1) {
      checked += 1
      const name = await boxes.nth(n).evaluate((el) => {
        const labelled = el.getAttribute('aria-labelledby')
        const byId = labelled ? (document.getElementById(labelled)?.textContent ?? '') : ''
        const own = el.getAttribute('aria-label') ?? ''
        const forLabel = el.id ? (document.querySelector(`label[for="${el.id}"]`)?.textContent ?? '') : ''
        return (own || byId || forLabel).trim()
      })
      if (name === '') nameless.push(`${path}: combobox #${n}`)
    }
  }
  expect(checked).toBeGreaterThan(0)
  expect(nameless).toEqual([])
})

test('gate: the signed-in account cannot be banned from its own row (disabled with the reason)', async ({ page, context }) => {
  await signInAs(context, 'admin')
  await open(page, '/users')
  const own = page.getByRole('row').filter({ hasText: 'admin@example.com' })
  await own.getByRole('button', { name: /^More actions for/ }).click()
  const ban = page.getByRole('menuitem', { name: /Ban user/ })
  await expect(ban).toHaveCount(1)
  await expect(ban).toHaveAttribute('aria-disabled', 'true')
  await expect(page.getByRole('menu')).toContainText('your own account')
  await page.getByTestId('users-search').fill('banme@example.com')
  const other = page.getByRole('row').filter({ hasText: 'banme@example.com' })
  await page.keyboard.press('Escape')
  await expect(page.getByRole('menu')).toHaveCount(0)
  await other.getByRole('button', { name: /^More actions for/ }).click()
  await expect(page.getByRole('menuitem', { name: /Ban user/ })).not.toHaveAttribute('aria-disabled', 'true')
})
