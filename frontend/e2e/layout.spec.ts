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

test('learner topic: a reading renders the writer\'s markdown (paragraphs, bold, table) with numbered citation marks and no evidence quotes', async ({ page }) => {
  await signInAs(page.context(), 'admin')
  await page.goto(`/learn/topics/${ids().learn_topic}`)
  const reading = page.locator('[data-block="reading"]').first()
  await expect(reading).toBeVisible()
  await expect(reading.locator('p')).toHaveCount(2)
  await expect(reading.locator('q')).toHaveCount(0)
  await expect(reading.locator('table tbody tr')).toHaveCount(2)
  await expect(reading.locator('strong')).toHaveText('alcohol rub')
  expect(await reading.textContent()).not.toMatch(/\*\*|⟦|\|---/)
  const mark = reading.locator('button.cite-mark').first()
  await expect(mark).toHaveText('1')
  expect((await mark.boundingBox())?.width ?? 999, 'a citation mark is a footnote number, not a chip').toBeLessThanOrEqual(30)
  await expect(page.locator('[data-block="reading"] ~ [data-testid="block-chips"]')).toHaveCount(0)
  await mark.click()
  await expect(page.getByTestId('passage-text')).toContainText('twenty seconds')
})

test('learner topic: the topic\'s term is marked once and opens its definition beside the text', async ({ page }) => {
  await signInAs(page.context(), 'admin')
  await page.goto(`/learn/topics/${ids().learn_topic}`)
  const marks = page.locator('[data-block="reading"] button.term-mark')
  await expect(marks).toHaveCount(1)
  await marks.first().click()
  await expect(page.getByTestId('companion-term')).toContainText('An alcohol rub takes twenty seconds')
  await expect(page.getByTestId('companion-glossary')).toHaveAttribute('href', `/learn/courses/${ids().learn}/glossary`)
})

// the translated lab binds no source, so the preview probe reads the fix lab (one approved, cited source)
for (const [name, path] of [['preview', () => `/courses/${ids().fix}/preview/sources`], ['learner', () => `/learn/courses/${ids().learn}/sources`]] as const) {
  test(`${name} sources page: every approved source under its citation number`, async ({ page }) => {
    await signInAs(page.context(), 'admin')
    await page.goto(path())
    const rows = page.getByTestId('source-row')
    await expect(rows.first()).toBeVisible()
    await expect(page.getByTestId('source-number').first()).toHaveText('1')
    await expect(page.locator('h1')).toHaveText('Sources')
  })
}

test('learner glossary: the term with the sentence that introduces it and the topic it comes from', async ({ page }) => {
  await signInAs(page.context(), 'admin')
  await page.goto(`/learn/courses/${ids().learn}/glossary`)
  await expect(page.getByTestId('glossary-term')).toHaveText(['alcohol rub'])
  await expect(page.getByTestId('glossary')).toContainText('An alcohol rub takes twenty seconds when hands are not visibly soiled.')
  await expect(page.getByTestId('glossary-topic')).toHaveAttribute('href', `/learn/topics/${ids().learn_topic}`)
})

test('learner course: the outline links to the sources page and the glossary with their counts', async ({ page }) => {
  await signInAs(page.context(), 'admin')
  await page.goto(`/learn/courses/${ids().learn}`)
  await expect(page.getByTestId('to-sources')).toHaveText('1 source')
  await expect(page.getByTestId('to-glossary')).toHaveText('1 term in the glossary')
})
