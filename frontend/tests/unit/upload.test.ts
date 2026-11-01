import { createServer, type Server } from 'node:http'
import type { AddressInfo } from 'node:net'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { createClient } from '../../src/api/client'
import { ENDPOINTS } from '../../src/api/endpoints'

let server: Server
let base = ''
const seen: { ctype: string | undefined; csrf: string | undefined; body: string }[] = []

beforeAll(async () => {
  server = createServer((req, res) => {
    const chunks: Buffer[] = []
    req.on('data', (c: Buffer) => chunks.push(c))
    req.on('end', () => {
      res.writeHead(200, { 'content-type': 'application/json' })
      if (req.url === ENDPOINTS.csrf) return void res.end(JSON.stringify({ csrf_token: 'tok' }))
      seen.push({ ctype: req.headers['content-type'], csrf: req.headers['x-csrf-token'] as string | undefined, body: Buffer.concat(chunks).toString() })
      res.end('{}')
    })
  })
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok))
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
})
afterAll(() => server.close())

describe('api client multipart', () => {
  it('sends FormData as multipart with the CSRF header and no JSON content type', async () => {
    const form = new FormData()
    form.append('file', new Blob(['hello source'], { type: 'text/plain' }), 'a.txt')
    await createClient({ baseUrl: base }).request('/api/v1/sources/upload', { method: 'POST', body: form, query: { media: 'text' } })
    expect(seen).toHaveLength(1)
    expect(seen[0]?.csrf).toBe('tok')
    expect(seen[0]?.ctype).toMatch(/^multipart\/form-data; boundary=/)
    expect(seen[0]?.body).toContain('hello source')
  })
})
