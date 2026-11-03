import type { Page } from '@playwright/test'
import { expect, ids, signInAs, test } from './fixtures'

// Owner finding 2026-11-03: the preview rendered topic titles one word per line and the
// lesson text in a 220px column; chips carried whole URLs; locked "study tools" sat in the
// companion. Presence checks never saw it: these probes measure geometry on the real build.
test.use({ viewport: { width: 1440, height: 900 } })

const box = async (page: Page, selector: string): Promise<{ x: number; width: number; height: number }> => {
  const b = await page.locator(selector).first().boundingBox()
  expect(b, selector).not.toBeNull()
  return b as { x: number; width: number; height: number }
}

for (const [name, path] of [['preview outline', () => `/courses/${ids().translated}/preview`], ['learner course', () => `/learn/courses/${ids().learn}`]] as const) {
  test(`${name}: a long topic title stays on one or two lines, never one word per line`, async ({ page }) => {
    await signInAs(page.context(), 'admin')
    await page.goto(path())
    const links = page.getByTestId('topic-open')
    await expect(links.first()).toBeVisible()
    const boxes = await links.evaluateAll((els) => els.map((el) => { const r = el.getBoundingClientRect(); return { width: r.width, height: r.height, text: el.textContent ?? '' } }))
    const long = boxes.filter((b) => b.text.split(' ').length >= 8)
    expect(long.length, 'the fixture must carry a long title or the probe proves nothing').toBeGreaterThan(0)
    for (const b of boxes) {
      expect(b.height, `${b.text}: height`).toBeLessThanOrEqual(56)
      if (b.text.split(' ').length >= 8) expect(b.width, `${b.text}: width`).toBeGreaterThan(300)
    }
  })
}

for (const [name, path] of [['preview topic', () => `/preview/topics/${ids().translated_topic}`], ['learner topic', () => `/learn/topics/${ids().learn_topic}`]] as const) {
  test(`${name}: the text column is wide and left of the companion; chips are compact; no locked tools`, async ({ page }) => {
    await signInAs(page.context(), 'admin')
    await page.goto(path())
    await expect(page.getByTestId('learn-block').first()).toBeVisible()
    const main = await box(page, '.learn-main')
    const side = await box(page, '[data-testid="companion"]')
    expect(main.width, 'text column').toBeGreaterThanOrEqual(560)
    expect(side.x, 'companion sits right of the text').toBeGreaterThanOrEqual(main.x + main.width)
    await expect(page.getByTestId('lesson-rail')).toBeVisible()
    const chips = await page.getByTestId('cite-chip').evaluateAll((els) => els.map((el) => ({ width: el.getBoundingClientRect().width, text: el.textContent ?? '' })))
    // the translated lab seeds no citation rows, so only the learner lab proves the chip form
    if (name === 'learner topic') expect(chips.length).toBeGreaterThan(0)
    for (const c of chips) {
      expect(c.width, c.text).toBeLessThanOrEqual(420)
      expect(c.text, 'a chip never shows a raw URL').not.toMatch(/https?:\/\//)
    }
    await expect(page.getByTestId(/^tool-/)).toHaveCount(0)
    await expect(page.getByText(/Not available yet/)).toHaveCount(0)
  })
}

test('learner topic: a reading keeps its paragraphs and shows no evidence quotes', async ({ page }) => {
  await signInAs(page.context(), 'admin')
  await page.goto(`/learn/topics/${ids().learn_topic}`)
  const reading = page.locator('[data-block="reading"]').first()
  await expect(reading).toBeVisible()
  await expect(reading.locator('p')).toHaveCount(2)
  await expect(reading.locator('q')).toHaveCount(0)
})
