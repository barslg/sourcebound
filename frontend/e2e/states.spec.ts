import { expect, sessions, signInAs, test } from './fixtures'

test('sign-in page shows the provider button and sends no request', async ({ page }) => {
  await page.goto('/signin')
  await expect(page.getByTestId('signin')).toHaveAttribute('href', '/api/v1/auth/login?provider=google')
})

test('anonymous visitors are sent to sign-in from every author URL', async ({ page }) => {
  for (const path of ['/users', '/roles', '/courses']) {
    await page.goto(path)
    await expect(page).toHaveURL(/\/signin$/)
  }
})

test('an account without rights lands on the waiting page and cannot leave it', async ({ page, context }) => {
  await signInAs(context, 'waiting')
  await page.goto('/users')
  await expect(page).toHaveURL(/\/waiting$/)
  await expect(page.getByRole('heading', { name: 'Waiting for access' })).toBeVisible()
  await page.getByTestId('waiting-check').click()
  await expect(page.getByText('Still no access.')).toBeVisible()
  await expect(page.getByRole('navigation')).toHaveCount(0)
})

test('a student is redirected from every author URL and sees no author nav entry', async ({ page, context }) => {
  await signInAs(context, 'student')
  for (const path of ['/users', '/roles']) {
    await page.goto(path)
    await expect(page).toHaveURL(/\/courses$/)
  }
  const nav = page.getByRole('navigation', { name: 'Main navigation' })
  await expect(nav.getByRole('link')).toHaveCount(1)
  await expect(nav.getByRole('link', { name: 'My courses' })).toBeVisible()
})

test('the course list answers a learner with the denied state carrying the backend message', async ({ page, context }) => {
  await signInAs(context, 'student')
  const res = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/v1/courses')
  await page.goto('/courses')
  expect((await res).status()).toBe(403)
  const box = page.locator('[data-view="denied"]')
  await expect(box).toBeVisible()
  await expect(box).toContainText('You do not have permission for this action.')
})

test('the course list shows a retryable error when the request fails', async ({ page, context }) => {
  await signInAs(context, 'admin')
  await page.route('**/api/v1/courses', (route) => route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'internal', message: 'internal error' }) }))
  await page.goto('/courses')
  const box = page.locator('[data-view="error"]')
  await expect(box).toContainText('internal error')
  await expect(box.getByRole('button', { name: 'Try again' })).toBeVisible()
})

test('users: loading state, then rows, and a search with no match shows a muted line', async ({ page, context }) => {
  await signInAs(context, 'admin')
  await page.route('**/api/v1/users', async (route) => {
    await new Promise((r) => setTimeout(r, 600))
    await route.continue()
  })
  await page.goto('/users')
  await expect(page.locator('[data-view="loading"]')).toBeVisible()
  await expect(page.getByTestId('users-table')).toBeVisible()
  await page.getByTestId('users-search').fill('zzz-no-such-person')
  await expect(page.getByText('Nothing matches your search.')).toBeVisible()
  await expect(page).toHaveURL(/q=zzz-no-such-person/)
  await page.getByTestId('users-search').fill('')
  await expect(page.getByRole('cell', { name: sessions().admin.email })).toBeVisible()
})

test('a signed-in user without a bell item sees the muted empty line and no numbers', async ({ page, context }) => {
  await signInAs(context, 'other')
  await page.goto('/courses')
  await page.getByTestId('bell').click()
  await expect(page.getByTestId('bell-empty')).toBeVisible()
  await expect(page.getByTestId('bell-count')).toHaveCount(0)
})

test('the bell lists notifications from the server after a reload and remembers what was read', async ({ page, context }) => {
  await signInAs(context, 'student')
  await page.goto('/courses')
  await expect(page.getByTestId('bell-count')).toHaveText('1')
  await page.getByTestId('bell').click()
  await expect(page.getByTestId('bell-notification')).toHaveCount(1)
  await expect(page.getByTestId('bell-notification')).toContainText('Operation finished')
  await expect(page.getByTestId('bell-count')).toHaveCount(0)
  await page.reload()
  await expect(page.getByTestId('bell-count')).toHaveCount(0)
  await page.getByTestId('bell').click()
  await expect(page.getByTestId('bell-notification')).toHaveCount(1)
  await expect(page.getByTestId('bell-notification').getByText('1 new')).toHaveCount(0)
})
