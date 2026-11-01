import { describe, expect, it } from 'vitest'
import { backoffMs, createEventsBus, type EventsEnv, type BusMessage } from '../../src/api/events'

class FakeSource {
  static all: FakeSource[] = []
  onmessage: ((e: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onopen: (() => void) | null = null
  closed = false
  constructor(public url: string) { FakeSource.all.push(this) }
  close() { this.closed = true }
}

function hub() {
  const members = new Set<{ onmessage: ((e: { data: BusMessage }) => void) | null }>()
  return (): ReturnType<EventsEnv['channel']> => {
    const me: { onmessage: ((e: { data: BusMessage }) => void) | null } = { onmessage: null }
    members.add(me)
    return {
      postMessage: (m: BusMessage) => members.forEach((o) => { if (o !== me) o.onmessage?.({ data: m }) }),
      set onmessage(fn) { me.onmessage = fn },
      get onmessage() { return me.onmessage },
      close: () => members.delete(me),
    }
  }
}

function env(over: Partial<EventsEnv> = {}) {
  FakeSource.all = []
  const timers: { fn: () => void; ms: number }[] = []
  const e: EventsEnv = {
    url: '/api/v1/events',
    source: (url) => new FakeSource(url) as unknown as ReturnType<EventsEnv['source']>,
    channel: hub(),
    acquireLeadership: async (run) => { await run() },
    schedule: (fn, ms) => { timers.push({ fn, ms }); return timers.length },
    jitter: () => 0,
    ...over,
  }
  return { e, timers }
}
const tick = () => new Promise((r) => setTimeout(r, 0))

describe('backoff', () => {
  it('doubles from 1 s and caps at 30 s', () => {
    expect([0, 1, 2, 3, 4, 5, 9].map((n) => backoffMs(n, () => 0))).toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000])
  })
})

describe('events bus', () => {
  it('opens one source as leader and delivers parsed messages', async () => {
    const { e } = env()
    const bus = createEventsBus(e)
    const got: BusMessage[] = []
    bus.subscribe((m) => got.push(m))
    bus.start()
    await tick()
    expect(FakeSource.all).toHaveLength(1)
    FakeSource.all[0]?.onmessage?.({ data: JSON.stringify({ operation_id: 'o1', status: 'done' }) })
    expect(got).toEqual([{ operation_id: 'o1', status: 'done' }])
  })

  it('relays leader messages to a follower tab without opening a source', async () => {
    const shared = hub()
    const a = env({ channel: shared })
    const b = env({ channel: shared, acquireLeadership: async () => new Promise<void>(() => {}) })
    const leader = createEventsBus(a.e)
    FakeSource.all = []
    const follower = createEventsBus(b.e)
    const got: BusMessage[] = []
    follower.subscribe((m) => got.push(m))
    leader.start(); follower.start()
    await tick()
    expect(FakeSource.all).toHaveLength(1)
    FakeSource.all[0]?.onmessage?.({ data: JSON.stringify({ operation_id: 'o2', status: 'queued' }) })
    expect(got).toEqual([{ operation_id: 'o2', status: 'queued' }])
  })

  it('reconnects with growing backoff and asks listeners to refetch after the reopen', async () => {
    const { e, timers } = env()
    const bus = createEventsBus(e)
    let reconnects = 0
    bus.onReconnect(() => { reconnects += 1 })
    bus.start(); await tick()
    FakeSource.all[0]?.onopen?.()
    expect(reconnects).toBe(0)
    FakeSource.all[0]?.onerror?.()
    expect(FakeSource.all[0]?.closed).toBe(true)
    expect(timers.map((t) => t.ms)).toEqual([1000])
    timers[0]?.fn()
    expect(FakeSource.all).toHaveLength(2)
    FakeSource.all[1]?.onerror?.()
    expect(timers.map((t) => t.ms)).toEqual([1000, 2000])
    timers[1]?.fn()
    FakeSource.all[2]?.onopen?.()
    expect(reconnects).toBe(1)
    FakeSource.all[2]?.onerror?.()
    expect(timers[2]?.ms).toBe(1000)
  })

  it('tells listeners when the session has ended', async () => {
    const { e } = env()
    const bus = createEventsBus(e)
    let ended = 0
    bus.onSessionEnded(() => { ended += 1 })
    bus.start(); await tick()
    FakeSource.all[0]?.onmessage?.({ data: JSON.stringify({ type: 'session.ended' }) })
    expect(ended).toBe(1)
    expect(FakeSource.all[0]?.closed).toBe(true)
  })

  it('stop closes the source and ignores later errors', async () => {
    const { e, timers } = env()
    const bus = createEventsBus(e)
    bus.start(); await tick()
    bus.stop()
    FakeSource.all[0]?.onerror?.()
    expect(FakeSource.all[0]?.closed).toBe(true)
    expect(timers).toHaveLength(0)
  })
})
