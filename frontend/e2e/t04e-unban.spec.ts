import { expect, ids, sessions, signInAs, test } from './fixtures'

test('a banned user offers Unban user in the row menu, and unbanning restores Ban user', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const s = sessions()
  const headers = { 'X-CSRF-Token': s.admin.csrf }
  const target = s.banme.id
  const banned = await page.request.post(`/api/v1/users/${target}/ban`, { headers, data: { reason: 'unban walk' } })
  expect([200, 409].includes(banned.status()) || banned.ok()).toBe(true)
  await page.goto('/users')
  await page.waitForLoadState('networkidle')
  const row = page.getByRole('row').filter({ hasText: s.banme.email })
  await expect(row).toContainText('Banned')
  await row.getByRole('button', { name: /^More actions for/ }).click()
  await expect(page.getByRole('menuitem', { name: /Ban user/ })).toHaveCount(0)
  await page.getByRole('menuitem', { name: /Unban user/ }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Bo Banme')
  const done = page.waitForResponse((r) => r.url().endsWith(`/users/${target}/unban`))
  await dialog.getByTestId('dialog-submit').click()
  expect((await done).ok()).toBe(true)
  await expect(row).not.toContainText('Banned')
  await row.getByRole('button', { name: /^More actions for/ }).click()
  await expect(page.getByRole('menuitem', { name: /Ban user/ })).toBeVisible()
  await expect(page.getByRole('menuitem', { name: /Unban user/ })).toHaveCount(0)
})

test('the scope picker offers existing courses and labels from the backend', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const tags = (await (await page.request.get('/api/v1/tags')).json()) as Array<{ tag: string; courses: Array<{ title: string }> }>
  expect(tags.length).toBeGreaterThan(0)
  await page.goto('/roles')
  await page.waitForLoadState('networkidle')
  await page.getByTestId('role-new').click()
  const picker = page.getByRole('dialog').getByTestId('scope-picker')
  await expect(picker).toBeVisible()
  await picker.locator('.p-multiselect').click()
  const options = await page.getByRole('option').allInnerTexts()
  for (const t of tags) for (const c of t.courses) expect(options.join('\n')).toContain(c.title)
})

test('run: a run stopped for missing sources shows a plain sentence and a way back to sources', async ({ page, context }) => {
  await signInAs(context, 'admin')
  const id = '00000000-0000-4000-8000-0000000000bb'
  await page.route(`**/api/v1/runs/${id}`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ id, status: 'stopped', course_id: ids().empty, compiled_dag: [], dag_digest: 'd', estimate: null, stop_marker: 'The planner returned no modules. Add sources with more content and try again.', stop_code: 'empty_plan', cost_so_far: '0', topics: [], steps: [{ step_key: 'architect:plan', status: 'failed', input_digest: null, attempt: 1, error: 'The planner returned no modules. Add sources with more content and try again.' }] }) }),
  )
  await page.goto(`/runs/${id}`)
  await expect(page.getByTestId('run-line')).toContainText('The planner returned no modules')
  expect(await page.locator('main').innerText()).not.toMatch(/ValidationError|\('modules',\)/)
  await page.getByTestId('run-back-sources').click()
  await expect(page).toHaveURL(new RegExp(`/courses/${ids().empty}/sources`))
})
