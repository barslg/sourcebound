// Cloudflare Worker in front of the static site: every /api/* request is forwarded to the API
// origin, so the browser, the session cookie and the CSRF token stay on one origin; every other
// path is answered from the built assets (wrangler.jsonc). API_ORIGIN is a project variable
// (README, Deploy).

interface AssetFetcher {
  fetch(request: Request): Promise<Response>
}

export interface Env {
  API_ORIGIN?: string
  ASSETS?: AssetFetcher
}

export async function proxyApi(request: Request, env: Env): Promise<Response> {
  const origin = env.API_ORIGIN
  if (!origin) return new Response('API_ORIGIN is not set for this deployment', { status: 503 })
  const incoming = new URL(request.url)
  const target = new URL(incoming.pathname + incoming.search, origin)
  const upstream = new Request(target.toString(), request)
  upstream.headers.set('x-forwarded-host', incoming.host)
  upstream.headers.set('x-forwarded-proto', incoming.protocol.replace(':', ''))
  return fetch(upstream, { redirect: 'manual' })
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (new URL(request.url).pathname.startsWith('/api/')) return proxyApi(request, env)
    if (env.ASSETS) return env.ASSETS.fetch(request)
    return new Response('Not found', { status: 404 })
  },
}
