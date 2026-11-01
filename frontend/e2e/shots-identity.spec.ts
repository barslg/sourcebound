import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { ids, signInAs, test } from './fixtures'

const OUT = join(dirname(fileURLToPath(import.meta.url)), '../../tmp/shots-identity')

test('identity screenshots at 1280 and 390 (set SHOTS_IDENTITY=1)', async ({ page, context }) => {
  test.skip(!process.env.SHOTS_IDENTITY, 'only when SHOTS_IDENTITY=1')
  test.setTimeout(600_000)
  mkdirSync(OUT, { recursive: true })
  const i = ids()
  const adminScreens: Array<[string, string]> = [
    ['courses', '/courses'],
    ['course-sources', `/courses/${i.sources}/sources`],
    ['plan', `/courses/${i.course}/plan`],
    ['sources', `/courses/${i.sources}/sources`],
    ['fix', `/courses/${i.fix}/fix`],
    ['publish', `/courses/${i.publish}/publish`],
    ['preview', `/courses/${i.publish}/preview`],
    ['runs', `/courses/${i.course}/runs`],
    ['run', `/runs/${i.run}`],
    ['report', `/courses/${i.publish}/report`],
    ['users', '/users'],
    ['invites', '/invites'],
    ['roles', '/roles'],
    ['not-found', '/no-such-page'],
  ]
  for (const width of [1280, 390]) {
    const height = width === 390 ? 844 : 900
    await page.setViewportSize({ width, height })
    await page.goto('/signin')
    await page.waitForLoadState('networkidle')
    await page.screenshot({ path: join(OUT, `signin-${width}.png`), fullPage: true })
    await page.goto('/invite/not-a-real-token')
    await page.waitForLoadState('networkidle')
    await page.screenshot({ path: join(OUT, `invite-${width}.png`), fullPage: true })
    await signInAs(context, 'waiting')
    await page.goto('/waiting')
    await page.waitForLoadState('networkidle')
    await page.screenshot({ path: join(OUT, `waiting-${width}.png`), fullPage: true })
    await signInAs(context, 'admin')
    for (const [name, path] of adminScreens) {
      await page.goto(path)
      await page.waitForLoadState('networkidle')
      await page.screenshot({ path: join(OUT, `${name}-${width}.png`), fullPage: true })
    }
    await page.goto('/courses')
    await page.waitForLoadState('networkidle')
    await page.getByTestId('bell').click()
    await page.waitForTimeout(400)
    await page.screenshot({ path: join(OUT, `bell-${width}.png`) })
    await page.keyboard.press('Escape')
    await signInAs(context, 'student')
    const learnScreens: Array<[string, string]> = [
      ['learn', '/learn'],
      ['learn-course', `/learn/courses/${i.learn}`],
      ['learn-topic', `/learn/topics/${i.learn_topic}`],
      ['learn-test', `/learn/topics/${i.learn_test}`],
    ]
    for (const [name, path] of learnScreens) {
      await page.goto(path)
      await page.waitForLoadState('networkidle')
      await page.screenshot({ path: join(OUT, `${name}-${width}.png`), fullPage: true })
    }
  }
})
