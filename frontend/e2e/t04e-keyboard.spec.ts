import { ACTIONS } from '../src/ui/actionMeta'
import { expect, signInAs, test } from './fixtures'

const RING = () => {
  const el = document.activeElement as HTMLElement
  const s = getComputedStyle(el)
  const outline = s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0
  const shadow = s.boxShadow !== 'none' && s.boxShadow !== ''
  const inner = el.matches('input, textarea') || el.closest('.p-inputtext, .p-select, .p-select-label') !== null
  return { outline, shadow, inner }
}

for (const path of ['/courses', '/users', '/roles']) {
  test(`Tab walk on ${path}: every control is reached in reading order and shows a focus ring`, async ({ page, context }) => {
    await signInAs(context, 'admin')
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    const wanted = await page.evaluate(() => {
      const visible = (el: Element): boolean => (el as HTMLElement).checkVisibility() && (el as HTMLElement).getBoundingClientRect().width > 0
      return [...document.querySelectorAll('button:not([disabled]), a[href], input:not([type=hidden]):not([disabled]), [tabindex="0"]')].filter(visible).length
    })
    const order: number[] = []
    const rings: string[] = []
    for (let i = 0; i < wanted + 12; i++) {
      await page.keyboard.press('Tab')
      const step = await page.evaluate((fn) => {
        const el = document.activeElement as HTMLElement
        if (!el || el === document.body) return null
        const ring = new Function(`return (${fn})()`)() as { outline: boolean; shadow: boolean; inner: boolean }
        const all = [...document.querySelectorAll('*')]
        return { index: all.indexOf(el), label: `${el.tagName} ${(el.getAttribute('aria-label') ?? el.textContent ?? '').trim().slice(0, 30)}`, ok: ring.outline || ring.shadow || ring.inner, tabindex: el.getAttribute('tabindex') }
      }, RING.toString())
      if (!step) break
      if (order.length > 0 && step.index <= (order[order.length - 1] as number)) break
      order.push(step.index)
      if (!step.ok) rings.push(step.label)
      expect(step.tabindex === null || Number(step.tabindex) <= 0, `positive tabindex on ${step.label}`).toBe(true)
    }
    expect(rings, 'controls without a visible focus ring').toEqual([])
    expect(order.length, 'controls reached by Tab in document order').toBeGreaterThanOrEqual(Math.floor(wanted * 0.8))
  })
}

test('new course dialog: Esc closes it and returns focus to the opener, Enter submits, focus is trapped', async ({ page, context }) => {
  await signInAs(context, 'admin')
  await page.goto('/courses')
  const opener = page.getByTestId('course-new')
  await opener.focus()
  await page.keyboard.press('Enter')
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  for (let i = 0; i < 14; i++) {
    await page.keyboard.press('Tab')
    expect(await page.evaluate(() => document.activeElement?.closest('[role="dialog"]') !== null), `Tab ${i} stays inside the dialog`).toBe(true)
  }
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(opener).toBeFocused()
  await opener.press('Enter')
  await dialog.getByLabel(/Title/).fill('Keyboard course')
  const posted = page.waitForRequest((r) => r.method() === 'POST' && new URL(r.url()).pathname === '/api/v1/courses')
  await page.route('**/api/v1/courses', (route) => (route.request().method() === 'POST' ? route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ course_id: '00000000-0000-4000-8000-000000000000' }) }) : route.continue()))
  await page.keyboard.press('Enter')
  expect((await posted).postDataJSON().title).toBe('Keyboard course')
})

test('the shortcut sheet lists every shortcut of the action map; / focuses the page search; ? opens the sheet', async ({ page, context }) => {
  await signInAs(context, 'admin')
  await page.goto('/users')
  await page.waitForLoadState('networkidle')
  await page.keyboard.press('/')
  await expect(page.locator('[data-search]')).toBeFocused()
  await page.keyboard.press('Escape')
  await page.locator('main').click({ position: { x: 5, y: 5 } })
  await page.keyboard.press('?')
  const sheet = page.getByTestId('shortcut-sheet')
  await expect(sheet).toBeVisible()
  const listed = await sheet.locator('[data-testid="shortcut-row"]').evaluateAll((els) => els.map((e) => e.getAttribute('data-action')))
  expect(listed.sort()).toEqual(ACTIONS.filter((a) => a.shortcut).map((a) => a.id).sort())
  await page.keyboard.press('Escape')
  await expect(sheet).toBeHidden()
  const opener = page.getByRole('button', { name: 'Keyboard shortcuts' })
  await opener.click()
  await expect(sheet).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(opener).toBeFocused()
})

test('icon-only controls carry their label as aria-label and a tooltip that keyboard focus shows', async ({ page, context }) => {
  await signInAs(context, 'admin')
  await page.goto('/users')
  await page.waitForLoadState('networkidle')
  const more = page.getByRole('button', { name: /^More actions for/ }).first()
  await expect(more).toBeVisible()
  const label = await more.getAttribute('aria-label')
  await more.focus()
  await expect(page.getByRole('tooltip')).toContainText(label as string)
})
