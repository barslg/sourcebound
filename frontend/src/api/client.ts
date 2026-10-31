import { ENDPOINTS } from './endpoints'

export class ApiError extends Error {
  readonly status: number
  readonly code: string | null
  constructor(status: number, message: string, code: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

export type Query = Record<string, string | number | boolean | undefined | null>

export interface RequestInitLite {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  query?: Query
  signal?: AbortSignal
  silent401?: boolean
}

export interface ClientOptions {
  baseUrl?: string
  fetchImpl?: typeof fetch
  onUnauthorized?: () => void
  onAccepted?: (operationId: string) => void
  sleep?: (ms: number) => Promise<void>
}

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])
const RETRY_STATUS = new Set([429, 502, 503, 504])
const MAX_GET_RETRIES = 2
const RETRY_DELAY_MS = 400

function messageOf(body: unknown, fallback: string): { message: string; code: string | null } {
  if (body && typeof body === 'object') {
    const rec = body as Record<string, unknown>
    const code = typeof rec.error === 'string' ? rec.error : null
    if (typeof rec.message === 'string' && rec.message) return { message: rec.message, code }
    if (typeof rec.detail === 'string' && rec.detail) return { message: rec.detail, code }
    if (Array.isArray(rec.detail) && rec.detail.length > 0) {
      const first = rec.detail[0] as { msg?: unknown }
      if (typeof first.msg === 'string') return { message: first.msg, code }
    }
  }
  return { message: fallback, code: null }
}

export function createClient(options: ClientOptions = {}) {
  const baseUrl = options.baseUrl ?? ''
  const doFetch = options.fetchImpl ?? ((input: RequestInfo | URL, init?: RequestInit) => fetch(input, init))
  const sleep = options.sleep ?? ((ms: number) => new Promise<void>((done) => setTimeout(done, ms)))
  let csrf: string | null = null

  async function csrfToken(): Promise<string> {
    if (csrf) return csrf
    const res = await doFetch(baseUrl + ENDPOINTS.csrf, { credentials: 'same-origin' })
    const body: unknown = await res.json().catch(() => null)
    if (!res.ok) {
      const { message, code } = messageOf(body, res.statusText)
      throw new ApiError(res.status, message, code)
    }
    csrf = (body as { csrf_token: string }).csrf_token
    return csrf
  }

  function resetCsrf(): void {
    csrf = null
  }

  async function request<T = unknown>(path: string, init: RequestInitLite = {}): Promise<T> {
    const method = init.method ?? 'GET'
    const unsafe = UNSAFE.has(method)
    let url = baseUrl + path
    if (init.query) {
      const q = new URLSearchParams()
      for (const [k, v] of Object.entries(init.query)) if (v !== undefined && v !== null && v !== '') q.set(k, String(v))
      const s = q.toString()
      if (s) url += (url.includes('?') ? '&' : '?') + s
    }
    const headers: Record<string, string> = { Accept: 'application/json' }
    let payload: BodyInit | undefined
    if (init.body instanceof FormData) {
      payload = init.body
    } else if (init.body !== undefined) {
      headers['Content-Type'] = 'application/json'
      payload = JSON.stringify(init.body)
    }
    if (unsafe) headers['X-CSRF-Token'] = await csrfToken()
    const maxRetries = unsafe ? 0 : MAX_GET_RETRIES
    for (let attempt = 0; ; attempt += 1) {
      let res: Response
      try {
        res = await doFetch(url, { method, headers, body: payload, credentials: 'same-origin', redirect: 'manual', signal: init.signal })
      } catch (cause) {
        if (init.signal?.aborted) throw cause
        if (attempt < maxRetries) {
          await sleep(RETRY_DELAY_MS * (attempt + 1))
          continue
        }
        throw new ApiError(0, cause instanceof Error ? cause.message : 'Network error')
      }
      if (RETRY_STATUS.has(res.status) && attempt < maxRetries) {
        await sleep(RETRY_DELAY_MS * (attempt + 1))
        continue
      }
      const text = res.status === 204 ? '' : await res.text()
      let parsed: unknown = null
      if (text) {
        try { parsed = JSON.parse(text) } catch { parsed = null }
      }
      if (!res.ok) {
        const { message, code } = messageOf(parsed, res.statusText || `HTTP ${res.status}`)
        if (res.status === 401) {
          resetCsrf()
          if (!init.silent401) options.onUnauthorized?.()
        }
        if (res.status === 403 && unsafe) resetCsrf()
        throw new ApiError(res.status, message, code)
      }
      if (res.status === 202 && parsed && typeof parsed === 'object') {
        const id = (parsed as { operation_id?: unknown }).operation_id
        if (typeof id === 'string') options.onAccepted?.(id)
      }
      return parsed as T
    }
  }

  return { request, resetCsrf }
}

let current = createClient()
let hooks: Pick<ClientOptions, 'onUnauthorized' | 'onAccepted'> = {}

export function configureClient(next: Pick<ClientOptions, 'onUnauthorized' | 'onAccepted'>): void {
  hooks = next
  current = createClient({ onUnauthorized: () => hooks.onUnauthorized?.(), onAccepted: (id) => hooks.onAccepted?.(id) })
}

export function request<T = unknown>(path: string, init?: RequestInitLite): Promise<T> {
  return current.request<T>(path, init)
}

export function resetCsrf(): void {
  current.resetCsrf()
}
