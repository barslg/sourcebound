import { afterEach, describe, expect, it, vi } from 'vitest'
import { onRequest } from '../../functions/api/[[path]]'

type Upstream = { request: Request; init: RequestInit | undefined }

function stubUpstream(response: Response): Upstream[] {
  const calls: Upstream[] = []
  vi.stubGlobal('fetch', async (request: Request, init?: RequestInit) => {
    calls.push({ request, init })
    return response
  })
  return calls
}

function only(calls: Upstream[]): Upstream {
  const [first] = calls
  if (!first || calls.length !== 1) throw new Error(`expected one upstream call, got ${calls.length}`)
  return first
}

afterEach(() => vi.unstubAllGlobals())

describe('the /api proxy of the Pages site', () => {
  it('answers 503 and names the missing variable when API_ORIGIN is not set', async () => {
    const calls = stubUpstream(new Response('never'))
    const res = await onRequest({ request: new Request('https://site.example.org/api/v1/auth/me'), env: {} })
    expect(res.status).toBe(503)
    expect(await res.text()).toContain('API_ORIGIN')
    expect(calls).toHaveLength(0)
  })

  it('forwards path, query, method, cookie and body to the origin and returns its answer', async () => {
    const calls = stubUpstream(new Response('{"ok":true}', { status: 201, headers: { 'content-type': 'application/json' } }))
    const request = new Request('https://site.example.org/api/v1/courses?page=2', {
      method: 'POST',
      headers: { cookie: '__Host-muse_session=abc', 'x-csrf-token': 't1', 'content-type': 'application/json' },
      body: '{"title":"x"}',
    })
    const res = await onRequest({ request, env: { API_ORIGIN: 'https://api.example.org' } })
    const sent = only(calls).request
    expect(sent.url).toBe('https://api.example.org/api/v1/courses?page=2')
    expect(sent.method).toBe('POST')
    expect(sent.headers.get('cookie')).toBe('__Host-muse_session=abc')
    expect(sent.headers.get('x-csrf-token')).toBe('t1')
    expect(sent.headers.get('x-forwarded-host')).toBe('site.example.org')
    expect(sent.headers.get('x-forwarded-proto')).toBe('https')
    expect(await sent.text()).toBe('{"title":"x"}')
    expect(res.status).toBe(201)
    expect(await res.text()).toBe('{"ok":true}')
  })

  it('hands the sign-in redirect back to the browser instead of following it', async () => {
    const calls = stubUpstream(new Response(null, { status: 302, headers: { location: 'https://accounts.example.org/o/oauth2/auth?state=s' } }))
    const res = await onRequest({
      request: new Request('https://site.example.org/api/v1/auth/login'),
      env: { API_ORIGIN: 'https://api.example.org/' },
    })
    const sent = only(calls)
    expect(sent.init?.redirect).toBe('manual')
    expect(sent.request.url).toBe('https://api.example.org/api/v1/auth/login')
    expect(res.status).toBe(302)
    expect(res.headers.get('location')).toBe('https://accounts.example.org/o/oauth2/auth?state=s')
  })
})
