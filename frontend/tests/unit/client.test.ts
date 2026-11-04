import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http'
import type { AddressInfo } from 'node:net'
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest'
import { createClient, ApiError } from '../../src/api/client'
import { ENDPOINTS } from '../../src/api/endpoints'

interface Seen { method: string; url: string; csrf: string | undefined }
let server: Server
let base = ''
let seen: Seen[] = []
let flaky = 0
type Handler = (req: IncomingMessage, res: ServerResponse) => void
const routes = new Map<string, Handler>()

beforeAll(async () => {
  server = createServer((req, res) => {
    seen.push({ method: req.method ?? '', url: req.url ?? '', csrf: req.headers['x-csrf-token'] as string | undefined })
    const handler = routes.get(`${req.method} ${(req.url ?? '').split('?')[0]}`)
    if (typeof handler !== 'function') {
      res.writeHead(404, { 'content-type': 'application/json' }).end(JSON.stringify({ detail: 'Not Found' }))
      return
    }
    handler(req, res)
  })
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok))
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
})
afterAll(() => server.close())
beforeEach(() => { seen = []; flaky = 0; routes.clear() })

const json = (res: ServerResponse, status: number, body: unknown) =>
  res.writeHead(status, { 'content-type': 'application/json' }).end(JSON.stringify(body))
const client = (extra = {}) => createClient({ baseUrl: base, sleep: async () => {}, ...extra })

describe('api client', () => {
  it('fetches the CSRF token once and sends it on unsafe methods only', async () => {
    routes.set(`GET ${ENDPOINTS.csrf}`, (_q, r) => json(r, 200, { csrf_token: 'tok-1' }))
    routes.set('POST /api/v1/x', (_q, r) => json(r, 200, { ok: true }))
    routes.set('GET /api/v1/y', (_q, r) => json(r, 200, { ok: true }))
    const c = client()
    await c.request('/api/v1/y')
    await c.request('/api/v1/x', { method: 'POST', body: { a: 1 } })
    await c.request('/api/v1/x', { method: 'POST', body: { a: 2 } })
    const posts = seen.filter((s) => s.method === 'POST')
    expect(posts.map((p) => p.csrf)).toEqual(['tok-1', 'tok-1'])
    expect(seen.filter((s) => s.url === ENDPOINTS.csrf)).toHaveLength(1)
    expect(seen.find((s) => s.method === 'GET' && s.url === '/api/v1/y')?.csrf).toBeUndefined()
  })

  it('retries a GET at most twice on 503 and then succeeds', async () => {
    routes.set('GET /api/v1/y', (_q, r) => (++flaky < 3 ? json(r, 503, { message: 'busy' }) : json(r, 200, { n: flaky })))
    expect(await client().request('/api/v1/y')).toEqual({ n: 3 })
    expect(seen).toHaveLength(3)
  })

  it('gives up after two retries and surfaces the backend message', async () => {
    routes.set('GET /api/v1/y', (_q, r) => json(r, 503, { error: 'x', message: 'still busy' }))
    const err = await client().request('/api/v1/y').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(503)
    expect((err as ApiError).message).toBe('still busy')
    expect(seen).toHaveLength(3)
  })

  it('never retries a GET that answered 500', async () => {
    routes.set('GET /api/v1/y', (_q, r) => json(r, 500, { message: 'boom' }))
    await expect(client().request('/api/v1/y')).rejects.toMatchObject({ status: 500, message: 'boom' })
    expect(seen).toHaveLength(1)
  })

  it('never retries a write', async () => {
    routes.set(`GET ${ENDPOINTS.csrf}`, (_q, r) => json(r, 200, { csrf_token: 't' }))
    routes.set('POST /api/v1/x', (_q, r) => json(r, 503, { message: 'busy' }))
    await expect(client().request('/api/v1/x', { method: 'POST', body: {} })).rejects.toMatchObject({ status: 503 })
    expect(seen.filter((s) => s.method === 'POST')).toHaveLength(1)
  })

  it('reads FastAPI detail when there is no message', async () => {
    await expect(client().request('/api/v1/missing')).rejects.toMatchObject({ status: 404, message: 'Not Found' })
  })

  it('calls onUnauthorized on 401 unless silent', async () => {
    routes.set('GET /api/v1/y', (_q, r) => json(r, 401, { error: 'unauthenticated', message: 'sign-in required' }))
    let calls = 0
    const c = client({ onUnauthorized: () => { calls += 1 } })
    await expect(c.request('/api/v1/y')).rejects.toMatchObject({ status: 401 })
    await expect(c.request('/api/v1/y', { silent401: true })).rejects.toMatchObject({ status: 401 })
    expect(calls).toBe(1)
  })

  it('registers the operation of a 202 answer', async () => {
    routes.set(`GET ${ENDPOINTS.csrf}`, (_q, r) => json(r, 200, { csrf_token: 't' }))
    routes.set('POST /api/v1/op', (_q, r) => json(r, 202, { operation_id: 'op-9' }))
    const accepted: string[] = []
    const out = await client({ onAccepted: (id: string) => accepted.push(id) }).request('/api/v1/op', { method: 'POST', body: {} })
    expect(accepted).toEqual(['op-9'])
    expect(out).toEqual({ operation_id: 'op-9' })
  })

  it('serialises query values and skips empty ones', async () => {
    routes.set('GET /api/v1/y', (_q, r) => json(r, 200, {}))
    await client().request('/api/v1/y', { query: { a: 'x y', b: undefined, c: false, d: 2 } })
    expect(seen[0]?.url).toBe('/api/v1/y?a=x+y&c=false&d=2')
  })
})
