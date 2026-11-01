import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { ids, signInAs, test } from './fixtures'

const OUT = join(dirname(fileURLToPath(import.meta.url)), '../../tmp/shots')

test('screenshots of every screen (set SHOTS=1)', async ({ page, context }) => {
  test.skip(!process.env.SHOTS, 'only when SHOTS=1')
  mkdirSync(OUT, { recursive: true })
  const i = ids()
  await page.setViewportSize({ width: 1360, height: 900 })
  await page.goto('/signin')
  await page.screenshot({ path: join(OUT, 'signin.png'), fullPage: true })
  await signInAs(context, 'admin')
  const screens: Array<[string, string]> = [
    ['courses', '/courses'], ['plan', `/courses/${i.course}/plan`], ['sources', `/courses/${i.sources}/sources`],
    ['fix', `/courses/${i.fix}/fix`], ['publish', `/courses/${i.publish}/publish`], ['run', `/runs/${i.run}`],
    ['users', '/users'], ['roles', '/roles'], ['invites', '/invites'],
  ]
  for (const [name, path] of screens) {
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    await page.screenshot({ path: join(OUT, `${name}.png`), fullPage: true })
  }
  await page.goto('/courses')
  await page.getByTestId('bell').click()
  await page.screenshot({ path: join(OUT, 'bell.png') })
})
