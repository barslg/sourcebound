import { expect, test } from './fixtures'
import type { Page } from '@playwright/test'

const origin = (): string => `http://127.0.0.1:${process.env.E2E_PREVIEW_PORT ?? '4173'}`
const LOGIN = '/api/v1/auth/login?provider=google'

async function status(page: Page): Promise<number> {
  return (await page.request.get('/api/v1/auth/me')).status()
}

async function cookieValue(page: Page): Promise<string | undefined> {
  return (await page.context().cookies()).find((c) => c.name === 'muse_session')?.value
}

async function signInViaButton(page: Page): Promise<string[]> {
  const seen: string[] = []
  page.on('request', (req) => {
    if (new URL(req.url()).pathname.startsWith('/api/v1/auth/')) seen.push(`${req.method()} ${req.resourceType()} ${req.isNavigationRequest() ? 'nav' : 'xhr'} ${new URL(req.url()).pathname}`)
  })
  await page.goto('/signin')
  await page.getByTestId('signin').click()
  await page.waitForURL((u) => !u.pathname.startsWith('/api/'), { timeout: 15000 })
  await expect.poll(() => status(page)).toBe(200)
  return seen
}

test('the button is a top-level navigation, not a fetch', async ({ page }) => {
  const seen = await signInViaButton(page)
  expect(seen.filter((x) => x.endsWith('/auth/login'))).toEqual(['GET document nav /api/v1/auth/login'])
  expect(seen.filter((x) => x.endsWith('/auth/callback'))).toEqual(['GET document nav /api/v1/auth/callback'])
})

test('the callback carries the cookie the 307 set', async ({ page }) => {
  let minted = ''
  let sent = ''
  page.on('response', async (res) => {
    if (new URL(res.url()).pathname !== '/api/v1/auth/login') return
    const raw = (await res.headersArray()).find((h) => h.name.toLowerCase() === 'set-cookie')?.value ?? ''
    minted = /muse_session=([^;]+)/.exec(raw)?.[1] ?? ''
  })
  page.on('request', async (req) => {
    if (new URL(req.url()).pathname !== '/api/v1/auth/callback') return
    sent = /muse_session=([^;]+)/.exec((await req.allHeaders()).cookie ?? '')?.[1] ?? ''
  })
  await signInViaButton(page)
  expect(minted).not.toBe('')
  expect(sent).toBe(minted)
})

test('sign in, sign out, sign in again', async ({ page }) => {
  await signInViaButton(page)
  const first = await cookieValue(page)
  await page.goto('/')
  await page.request.post('/api/v1/auth/logout', { headers: { 'X-CSRF-Token': (await (await page.request.get('/api/v1/auth/csrf')).json()).csrf_token } })
  expect(await status(page)).toBe(401)
  await page.goto('/signin')
  await page.getByTestId('signin').click()
  await page.waitForURL((u) => !u.pathname.startsWith('/api/'), { timeout: 15000 })
  await expect.poll(() => status(page)).toBe(200)
  expect(await cookieValue(page)).not.toBe(first)
})

test('starting a sign-in while already signed in finishes and keeps the account signed in', async ({ page }) => {
  await signInViaButton(page)
  await page.goto(LOGIN)
  await page.waitForURL((u) => !u.pathname.startsWith('/api/'), { timeout: 15000 })
  expect(await status(page)).toBe(200)
})

test('a repeated callback with a spent state answers a plain redirect home, not 401', async ({ page }) => {
  let callback = ''
  page.on('request', (req) => {
    if (new URL(req.url()).pathname === '/api/v1/auth/callback') callback = req.url()
  })
  await signInViaButton(page)
  expect(callback).not.toBe('')
  const again = await page.request.get(callback, { maxRedirects: 0 })
  expect(again.status()).toBe(303)
  expect(again.headers().location).toBe('/')
  expect(await status(page)).toBe(200)
})

test('a spent state without any session is still refused', async ({ page, playwright, baseURL }) => {
  let callback = ''
  page.on('request', (req) => {
    if (new URL(req.url()).pathname === '/api/v1/auth/callback') callback = req.url()
  })
  await signInViaButton(page)
  const anon = await playwright.request.newContext({ baseURL })
  const res = await anon.get(callback, { maxRedirects: 0 })
  expect(res.status()).toBe(401)
  await anon.dispose()
})

test('a stale or foreign session cookie does not break the sign-in', async ({ page, browser }) => {
  await page.context().addCookies([{ name: 'muse_session', value: 'not-a-session', url: origin() }])
  await signInViaButton(page)
  const mine = await cookieValue(page)
  const second = await browser.newContext()
  await second.addCookies([{ name: 'muse_session', value: mine ?? '', url: origin() }])
  const page2 = await second.newPage()
  await page2.goto(LOGIN)
  await page2.waitForURL((u) => !u.pathname.startsWith('/api/'), { timeout: 15000 })
  expect(await status(page2)).toBe(200)
  expect(await cookieValue(page2)).not.toBe(mine)
  await second.close()
})

test('a callback whose state belongs to another session is refused', async ({ page, playwright, baseURL }) => {
  await signInViaButton(page)
  const live = (await cookieValue(page)) ?? ''
  const starter = await playwright.request.newContext({ baseURL })
  const started = await starter.get(LOGIN, { maxRedirects: 0 })
  expect(started.status()).toBe(307)
  const state = new URL(started.headers().location ?? 'http://none').searchParams.get('state') ?? ''
  expect(state).not.toBe('')
  const url = `/api/v1/auth/callback?state=${encodeURIComponent(state)}&code=x`
  const bare = await playwright.request.newContext({ baseURL })
  const res = await bare.get(url, { maxRedirects: 0 })
  expect(res.status()).toBe(401)
  expect((await res.json()).message).toContain('without its session cookie')
  const foreign = await playwright.request.newContext({ baseURL, storageState: { origins: [], cookies: [{ name: 'muse_session', value: live, domain: '127.0.0.1', path: '/', expires: -1, httpOnly: true, secure: false, sameSite: 'Lax' }] } })
  const res2 = await foreign.get(url, { maxRedirects: 0 })
  expect(res2.status()).toBe(401)
  expect((await res2.json()).message).toContain('another session')
  await Promise.all([starter.dispose(), bare.dispose(), foreign.dispose()])
})
