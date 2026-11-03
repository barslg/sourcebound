// Cloudflare Pages Function: every /api/* request of the static site is forwarded to the
// API origin, so the browser, the session cookie and the CSRF token stay on one origin.
// Called by the Pages runtime; API_ORIGIN is a project variable (README, Deploy).

interface Env {
  API_ORIGIN?: string
}

interface Context {
  request: Request
  env: Env
}

export async function onRequest({ request, env }: Context): Promise<Response> {
  const origin = env.API_ORIGIN
  if (!origin) return new Response('API_ORIGIN is not set for this deployment', { status: 503 })
  const incoming = new URL(request.url)
  const target = new URL(incoming.pathname + incoming.search, origin)
  const upstream = new Request(target.toString(), request)
  upstream.headers.set('x-forwarded-host', incoming.host)
  upstream.headers.set('x-forwarded-proto', incoming.protocol.replace(':', ''))
  return fetch(upstream, { redirect: 'manual' })
}
