import AxeBuilder from '@axe-core/playwright'
import { expect, ids, signInAs, test } from './fixtures'

const EMOJI_OR_ARROW = /[\p{Extended_Pictographic}←-⇿⟵-⟹⤀-⥿]/u

for (const theme of ['light', 'dark'] as const) {
  test(`axe finds no serious or critical violation in the ${theme} theme`, async ({ page, context }) => {
    await context.addInitScript((value) => localStorage.setItem('sourcebound.theme', value), theme)
    await signInAs(context, 'admin')
    for (const path of ['/users', '/roles', '/courses', `/courses/${ids().course}/plan`, `/courses/${ids().course}/sources`, `/courses/${ids().course}/publish`, `/courses/${ids().sources}/sources`, `/courses/${ids().fix}/fix`, `/runs/${ids().run}`, `/runs/${ids().live_run}`]) {
      await page.goto(path)
      await expect(page.locator('main')).toBeVisible()
      await page.waitForLoadState('networkidle')
      expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe(theme)
      const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()
      const bad = result.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
      expect(bad.map((v) => `${path} ${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([])
    }
    await context.clearCookies()
    await page.goto('/signin')
    const anon = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()
    expect(anon.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => v.id)).toEqual([])
  })
}

test('pseudo-locale brackets every interface string and no page has emoji or arrows', async ({ page, context }) => {
  await page.goto('/signin?lang=pseudo')
  for (const sel of ['h1', 'h2', 'p', 'a.p-button']) {
    for (const text of await page.locator(`main ${sel}`).allInnerTexts()) expect(text, sel).toMatch(/^\[.*\]$/s)
  }
  await signInAs(context, 'admin')
  await page.goto('/roles?lang=pseudo')
  await expect(page.getByTestId('roles-table')).toBeVisible()
  await page.goto(`/courses/${ids().fix}/fix?lang=pseudo`)
  await expect(page.getByTestId('held-row').first()).toBeVisible()
  for (const text of await page.locator('main h2, main button').allInnerTexts()) {
    if (text.trim()) expect(text.trim(), text).toMatch(/\[.*\]/s)
  }
  await page.goto(`/runs/${ids().run}?lang=pseudo`)
  await expect(page.getByTestId('run')).toBeVisible()
  for (const text of await page.locator('main h1, main button, main .hint').allInnerTexts()) {
    if (text.trim()) expect(text.trim(), text).toMatch(/^\[.*\]$/s)
  }
  await page.goto('/roles?lang=pseudo')
  await expect(page.getByTestId('roles-table')).toBeVisible()
  const chrome = page.locator('header button, nav a, h1, th, main > div button')
  for (const text of await chrome.allInnerTexts()) {
    if (text.trim()) expect(text.trim(), text).toMatch(/\[.*\]/s)
  }
  for (const path of ['/users', '/roles', '/courses', '/signin', `/courses/${ids().course}/plan`, `/runs/${ids().run}`]) {
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    expect(EMOJI_OR_ARROW.test(await page.locator('body').innerText()), path).toBe(false)
  }
})
