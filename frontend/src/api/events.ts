import { ENDPOINTS } from './endpoints'

export interface BusMessage {
  type?: string
  operation_id?: string
  status?: string
}

interface SourceLike {
  onmessage: ((e: { data: string }) => void) | null
  onerror: (() => void) | null
  onopen: (() => void) | null
  close(): void
}
interface ChannelLike {
  postMessage(m: BusMessage): void
  onmessage: ((e: { data: BusMessage }) => void) | null
  close(): void
}

export interface EventsEnv {
  url: string
  source: (url: string) => SourceLike
  channel: () => ChannelLike
  acquireLeadership: (run: () => Promise<void>) => Promise<void>
  schedule: (fn: () => void, ms: number) => unknown
  jitter?: () => number
}

const MIN_MS = 1000
const MAX_MS = 30000

export function backoffMs(attempt: number, jitter: () => number = Math.random): number {
  const base = Math.min(MAX_MS, MIN_MS * 2 ** attempt)
  return Math.min(MAX_MS, base + Math.floor(jitter() * 250))
}

export function createEventsBus(env: EventsEnv) {
  const listeners = new Set<(m: BusMessage) => void>()
  const reconnectListeners = new Set<() => void>()
  const endedListeners = new Set<() => void>()
  let channel: ChannelLike | null = null
  let source: SourceLike | null = null
  let stopped = true
  let attempt = 0
  let everOpened = false
  let release: (() => void) | null = null

  function deliver(m: BusMessage): void {
    if (m.type === 'session.ended') {
      endedListeners.forEach((fn) => fn())
      return
    }
    listeners.forEach((fn) => fn(m))
  }

  function open(): void {
    if (stopped) return
    const s = env.source(env.url)
    source = s
    s.onopen = () => {
      if (everOpened && attempt > 0) reconnectListeners.forEach((fn) => fn())
      everOpened = true
      attempt = 0
    }
    s.onmessage = (e) => {
      let parsed: BusMessage
      try { parsed = JSON.parse(e.data) as BusMessage } catch { return }
      if (parsed.type === 'session.ended') {
        stopped = true
        s.close()
        release?.()
      }
      channel?.postMessage(parsed)
      deliver(parsed)
    }
    s.onerror = () => {
      s.close()
      if (stopped) return
      const wait = backoffMs(attempt, env.jitter)
      attempt += 1
      env.schedule(open, wait)
    }
  }

  return {
    subscribe(fn: (m: BusMessage) => void): () => void {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    onReconnect(fn: () => void): () => void {
      reconnectListeners.add(fn)
      return () => reconnectListeners.delete(fn)
    },
    onSessionEnded(fn: () => void): () => void {
      endedListeners.add(fn)
      return () => endedListeners.delete(fn)
    },
    start(): void {
      if (!stopped) return
      stopped = false
      attempt = 0
      channel = env.channel()
      channel.onmessage = (e) => deliver(e.data)
      void env.acquireLeadership(
        () => new Promise<void>((done) => {
          release = done
          open()
        }),
      )
    },
    stop(): void {
      stopped = true
      source?.close()
      source = null
      channel?.close()
      channel = null
      release?.()
      release = null
    },
  }
}

export function browserEnv(): EventsEnv {
  return {
    url: ENDPOINTS.events,
    source: (url) => new EventSource(url, { withCredentials: true }) as unknown as SourceLike,
    channel: () => new BroadcastChannel('sourcebound-events') as unknown as ChannelLike,
    acquireLeadership: async (run) => {
      if ('locks' in navigator) {
        await navigator.locks.request('sourcebound-events-leader', run)
      } else {
        await run()
      }
    },
    schedule: (fn, ms) => setTimeout(fn, ms),
  }
}

export const eventsBus = createEventsBus(browserEnv())
