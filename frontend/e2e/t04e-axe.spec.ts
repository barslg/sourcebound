import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { expect, ids, signInAs, test } from './fixtures'

function routes(): string[] {
  const i = ids()
  return ['/courses', '/users', '/roles', '/invites', `/courses/${i.course}/plan`, `/courses/${i.course}/sources`, `/courses/${i.course}/generate`, `/courses/${i.fix}/fix`, `/courses/${i.publish}/publish`, `/courses/${i.translated}/publish`, `/courses/${i.translated}/preview?language=nb`, `/preview/topics/${i.translated_topic}?language=nb`, `/runs/${i.run}`, `/learn/courses/${i.learn}`, `/learn/topics/${i.learn_topic}`, `/learn/topics/${i.learn_test}`, `/courses/${i.learn}/report`, '/signin', `/invite/${'ab'.repeat(32)}`, '/waiting', '/nowhere-at-all']
}

async function bad(page: Page): Promise<string[]> {
  const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  return result.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 3).join(', ')}`)
}

for (const who of ['admin', 'student', 'anonymous'] as const) {
  test(`axe: every route as ${who} has no serious or critical violation, landmarks, alt text and lang`, async ({ page, context }) => {
    if (who !== 'anonymous') await signInAs(context, who)
    const problems: string[] = []
    for (const path of routes()) {
      await page.goto(path)
      await page.waitForLoadState('networkidle')
      for (const v of await bad(page)) problems.push(`${who} ${path} ${v}`)
      const facts = await page.evaluate(() => ({
        main: document.querySelectorAll('main').length,
        header: document.querySelectorAll('header').length,
        nav: document.querySelectorAll('nav').length,
        footer: document.querySelectorAll('footer').length,
        shell: document.querySelector('.shell') !== null,
        noAlt: [...document.querySelectorAll('img')].filter((i) => !i.hasAttribute('alt')).length,
        lang: document.documentElement.lang,
      }))
      if (facts.main !== 1) problems.push(`${who} ${path} has ${facts.main} main landmarks`)
      if (facts.shell && (facts.header < 1 || facts.nav < 1 || facts.footer < 1)) problems.push(`${who} ${path} lacks header/nav/footer`)
      if (facts.noAlt > 0) problems.push(`${who} ${path} has ${facts.noAlt} images without alt`)
      if (facts.lang !== 'en') problems.push(`${who} ${path} lang is ${facts.lang}`)
    }
    expect(problems).toEqual([])
  })
}

test('the language attribute follows the chosen locale', async ({ page, context }) => {
  await signInAs(context, 'admin')
  for (const lang of ['nb', 'uk', 'en']) {
    await page.goto(`/courses?lang=${lang}`)
    expect(await page.evaluate(() => document.documentElement.lang)).toBe(lang)
  }
})
