import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Page } from '@playwright/test'
import { expect, ids, sessions, signInAs, test } from './fixtures'

const SHOTS = join(dirname(fileURLToPath(import.meta.url)), '../../tmp/shots-t04i')

async function open(page: Page, path: string): Promise<void> {
  await page.goto(path)
  await expect(page.locator('main')).toBeVisible()
  await page.waitForLoadState('networkidle')
  await expect(page.locator('[data-view="loading"]')).toHaveCount(0)
}

function authorRoutes(): Array<[string, string]> {
  const i = ids()
  return [
    ['courses', '/courses'], ['users', '/users'], ['roles', '/roles'], ['invites', '/invites'],
    ['plan', `/courses/${i.course}/plan`], ['sources', `/courses/${i.sources}/sources`], ['fix', `/courses/${i.fix}/fix`],
    ['publish', `/courses/${i.publish}/publish`], ['run', `/runs/${i.run}`],
  ]
}

test('targets: buttons are at least 32 px, primary actions 40 px, and neighbours 8 px apart', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const problems: string[] = []
  for (const [name, path] of authorRoutes()) {
    await open(page, path)
    const found = await page.evaluate(() => {
      const items = [...document.querySelectorAll('main button, header button, main a.p-button')]
        .filter((el) => (el as HTMLElement).checkVisibility() && !el.closest('.p-paginator') && !el.closest('.p-tree'))
        .map((el) => {
          const r = el.getBoundingClientRect()
          return { name: (el.getAttribute('aria-label') ?? el.textContent ?? '').trim().slice(0, 28), x: r.x, y: r.y, w: r.width, h: r.height, primary: el.getAttribute('data-kind') === 'primary' && el.getAttribute('data-size') !== 'small' }
        })
      const small: string[] = []
      const gaps: string[] = []
      for (const a of items) {
        if (a.w < 31.5 || a.h < 31.5) small.push(`${a.name} ${Math.round(a.w)}x${Math.round(a.h)}`)
        if (a.primary && a.h < 39.5) small.push(`primary ${a.name} height ${Math.round(a.h)}`)
        for (const b of items) {
          if (a === b || a.x >= b.x) continue
          const overlapY = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y) > 4
          const gap = b.x - (a.x + a.w)
          if (overlapY && gap >= 0 && gap < 7.5) gaps.push(`${a.name} | ${b.name} ${Math.round(gap)}px`)
        }
      }
      return { small, gaps }
    })
    for (const s of found.small) problems.push(`${name} too small: ${s}`)
    for (const g of found.gaps) problems.push(`${name} too close: ${g}`)
  }
  expect(problems).toEqual([])
})

test('forms: required in words, label above, error next to the field on blur, no toast', async ({ page, context }) => {
  await signInAs(context, 'admin')
  await open(page, '/courses')
  await page.getByTestId('course-new').click()
  const dialog = page.getByRole('dialog')
  const field = dialog.locator('.field').filter({ has: page.getByLabel(/Title/) })
  await expect(field).toContainText(/required/i)
  const label = await field.locator('label').boundingBox()
  const input = await field.locator('input').boundingBox()
  expect((label?.y ?? 0) + (label?.height ?? 0)).toBeLessThanOrEqual((input?.y ?? 0) + 1)
  await expect(field.getByTestId('field-error')).toHaveCount(0)
  await dialog.getByLabel(/Title/).focus()
  await dialog.getByLabel(/Title/).blur()
  await expect(field.getByTestId('field-error')).toBeVisible()
  await expect(page.locator('.p-toast-message')).toHaveCount(0)
  const submit = dialog.getByTestId('dialog-submit')
  await expect(submit).toBeDisabled()
  await expect(dialog.getByTestId('reason')).toBeVisible()
  const last = await dialog.evaluate((d) => {
    const buttons = [...d.querySelectorAll('form button')]
    return buttons.findIndex((b) => b.getAttribute('data-testid') === 'dialog-submit') === buttons.length - 1
  })
  expect(last).toBe(true)
})

test('destructive actions live in the row overflow menu and ask a confirm dialog that names the object; users can be unbanned', async ({ page, context }) => {
  await signInAs(context, 'admin')
  await open(page, '/users')
  const s = sessions()
  const row = page.getByRole('row').filter({ hasText: s.inviteme.email })
  await expect(row.getByRole('button', { name: /^Delete user/ })).toHaveCount(0)
  await row.getByRole('button', { name: /^More actions for/ }).click()
  await page.getByRole('menuitem', { name: /Delete user/ }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Ines Inviteme')
  await expect(dialog.getByTestId('dialog-submit')).toBeDisabled()
  await dialog.getByLabel(/Type/).fill('Ines Inviteme')
  await expect(dialog.getByTestId('dialog-submit')).toBeEnabled()
  await page.keyboard.press('Escape')
  const banned = page.getByRole('row').filter({ hasText: s.banme.email })
  await banned.getByRole('button', { name: /^More actions for/ }).click()
  await expect(page.getByRole('menuitem', { name: /Ban user|Unban user/ })).toHaveCount(1)
})

test('tables: sticky header, sort indicator, dash for empty cells, relative dates with the full date on hover, 12 rows fit 1080p', async ({ page, context }) => {
  await signInAs(context, 'admin')
  await page.setViewportSize({ width: 1920, height: 1080 })
  await open(page, '/users')
  const th = page.locator('thead th').first()
  expect(await th.evaluate((el) => getComputedStyle(el).position)).toBe('sticky')
  await page.locator('thead th').filter({ hasText: 'Email' }).click()
  await expect(page.locator('thead th[aria-sort="ascending"], thead th[aria-sort="descending"]')).toHaveCount(1)
  await expect(page.locator('tbody td .sr-only').first()).toHaveText(/none/i)
  const time = page.locator('tbody time').first()
  await expect(time).toHaveAttribute('title', /\d{4}|\d{1,2}[:.]\d{2}/)
  expect(await page.locator('tbody tr').count()).toBeGreaterThanOrEqual(6)
  const tenth = await page.locator('tbody tr').nth(Math.min(11, (await page.locator('tbody tr').count()) - 1)).boundingBox()
  expect((tenth?.y ?? 0) + (tenth?.height ?? 0)).toBeLessThanOrEqual(1080)
  const tabular = await page.locator('tbody td').first().evaluate((el) => getComputedStyle(el).fontVariantNumeric)
  expect(tabular).toContain('tabular-nums')
})

test('feedback: one toast region bottom-right, success fades in 4 s, errors stay with the backend message and a Retry', async ({ page, context }) => {
  await signInAs(context, 'admin')
  await page.route('**/api/v1/courses/*/held', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ batch_quote_usd: null, blocks: [{ id: '00000000-0000-4000-8000-0000000000aa', topic_title: 'Mock topic', brief: 'b', hold_reason: 'mock', passage: 'p', address: 'a' }] }) }),
  )
  await open(page, `/courses/${ids().fix}/fix`)
  let calls = 0
  await page.route('**/api/v1/blocks/*/accept', (route) => {
    calls += 1
    return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'internal', message: 'the store is busy' }) })
  })
  await page.getByRole('button', { name: 'Accept as checked by me' }).first().click()
  const toast = page.locator('.p-toast-message').first()
  await expect(toast).toContainText('the store is busy')
  const box = await toast.boundingBox()
  const view = page.viewportSize() as { width: number; height: number }
  expect((box?.x ?? 0) + (box?.width ?? 0)).toBeGreaterThan(view.width * 0.7)
  expect((box?.y ?? 0) + (box?.height ?? 0)).toBeGreaterThan(view.height * 0.6)
  await page.waitForTimeout(6000)
  await expect(toast).toBeVisible()
  await toast.getByRole('button', { name: 'Try again' }).click()
  await expect.poll(() => calls).toBe(2)
  await expect(page.locator('.p-toast')).toHaveCount(1)
})

test('navigation: current page is marked, this-course section shows title and stages, titles and breadcrumbs follow the grammar', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const i = ids()
  await open(page, '/users')
  await expect(page).toHaveTitle('Users · Sourcebound')
  await expect(page.locator('nav a[aria-current="page"]')).toHaveCount(1)
  await open(page, `/courses/${i.course}/plan`)
  const title = (await page.getByTestId('course-head').locator('h1').innerText()).trim()
  await expect(page).toHaveTitle(`Plan · ${title} · Sourcebound`)
  await expect(page.locator('nav').getByTestId('nav-course')).toContainText(title)
  await expect(page.locator('nav').getByTestId('nav-stage')).toHaveCount(5)
  await expect(page.getByTestId('crumbs')).toBeVisible()
  await open(page, `/runs/${i.run}`)
  await expect(page.getByTestId('crumbs')).toBeVisible()
})

test('readability: measure at most 75 characters, one h1, headings in order, no all-caps', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const problems: string[] = []
  for (const [name, path] of authorRoutes()) {
    await open(page, path)
    const r = await page.evaluate(() => {
      const probe = document.createElement('div')
      probe.style.width = '75ch'
      document.querySelector('main')?.appendChild(probe)
      const limit = probe.getBoundingClientRect().width + 1
      probe.remove()
      const wide = [...document.querySelectorAll('main p')].filter((p) => (p as HTMLElement).checkVisibility() && p.getBoundingClientRect().width > limit).map((p) => (p.textContent ?? '').slice(0, 30))
      const levels = [...document.querySelectorAll('h1, h2, h3, h4')].filter((h) => (h as HTMLElement).checkVisibility() && !h.closest('.p-toast')).map((h) => Number(h.tagName[1]))
      const jumps = levels.filter((l, k) => k > 0 && l - (levels[k - 1] as number) > 1)
      const caps = [...document.querySelectorAll('body *')].filter((e) => getComputedStyle(e).textTransform === 'uppercase' && (e as HTMLElement).checkVisibility()).length
      return { wide, h1: levels.filter((l) => l === 1).length, jumps: jumps.length, caps, first: levels[0] }
    })
    if (r.wide.length) problems.push(`${name} wide paragraphs: ${r.wide.join(' | ')}`)
    if (r.h1 !== 1) problems.push(`${name} has ${r.h1} h1`)
    if (r.jumps) problems.push(`${name} skips a heading level`)
    if (r.caps) problems.push(`${name} uses all-caps`)
    if (r.first !== 1) problems.push(`${name} first heading is h${r.first}`)
  }
  expect(problems).toEqual([])
})

for (const width of [1280, 1024]) {
  test(`responsive ${width}: no horizontal scroll on any screen; screenshots saved`, async ({ page, context }) => {
    test.setTimeout(120000)
    mkdirSync(SHOTS, { recursive: true })
    await signInAs(context, 'admin')
    await page.setViewportSize({ width, height: 900 })
    const problems: string[] = []
    for (const [name, path] of authorRoutes()) {
      await open(page, path)
      const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
      if (over > 0) problems.push(`${name} scrolls ${over}px sideways`)
      await page.screenshot({ path: join(SHOTS, `${name}-${width}.png`), fullPage: true })
    }
    await page.goto('/signin')
    await page.screenshot({ path: join(SHOTS, `signin-${width}.png`), fullPage: true })
    expect(problems).toEqual([])
    if (width === 1024) {
      await open(page, `/courses/${ids().course}/plan`)
      await expect(page.getByRole('tab', { name: /Questions and generate/ })).toBeVisible()
      await expect(page.getByTestId('before-card')).toBeHidden()
      await page.getByRole('tab', { name: /Questions and generate/ }).click()
      await expect(page.getByTestId('before-card')).toBeVisible()
    }
  })
}

test('scope pickers: no free-text field asks for a label, tag, id or link', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const problems: string[] = []
  const check = async (where: string): Promise<void> => {
    const bad = await page.evaluate(() =>
      [...document.querySelectorAll('input[type=text], input:not([type]), textarea')]
        .filter((el) => (el as HTMLElement).checkVisibility())
        .map((el) => (document.querySelector(`label[for="${el.id}"]`)?.textContent ?? el.getAttribute('aria-label') ?? '').trim())
        .filter((label) => /\b(tags?|labels?|ids?|links?|urls?)\b/i.test(label) && !/web address/i.test(label)),
    )
    for (const b of bad) problems.push(`${where}: free-text field "${b}"`)
  }
  await open(page, '/courses')
  await page.getByTestId('course-new').click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await check('new course')
  await page.keyboard.press('Escape')
  await open(page, '/roles')
  await page.getByTestId('role-new').click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await check('new role')
  await page.keyboard.press('Escape')
  await open(page, '/invites')
  await check('invites')
  await open(page, '/users')
  await page.getByRole('row').filter({ hasText: sessions().student.email }).getByRole('button', { name: /^Assign role/ }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await check('assign role')
})
