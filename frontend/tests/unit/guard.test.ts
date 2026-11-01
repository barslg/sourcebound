import { describe, expect, it } from 'vitest'
import { decide, firstAllowed, type GuardSession, type SectionEntry } from '../../src/router/guard'

const sections: SectionEntry[] = [
  { path: '/courses', perm: 'section.courses' },
  { path: '/sources', perm: 'section.sources' },
  { path: '/users', perm: 'section.users' },
]
const session = (codes: string[], over: Partial<GuardSession> = {}): GuardSession => ({
  signedIn: true, isSuperadmin: false, permissions: codes, ...over,
})
const to = (path: string, meta: { perm?: string; public?: boolean } = {}) => ({ path, meta })

describe('router guard', () => {
  it('lets anyone open a public route', () => {
    expect(decide(to('/signin', { public: true }), session([], { signedIn: false }), sections)).toBe(true)
  })
  it('sends a signed-in user away from sign-in', () => {
    expect(decide(to('/signin', { public: true }), session(['section.sources']), sections)).toBe('/sources')
  })
  it('sends an anonymous visitor to sign-in', () => {
    expect(decide(to('/users', { perm: 'section.users' }), session([], { signedIn: false }), sections)).toBe('/signin')
  })
  it('parks an account without any permission on the waiting page', () => {
    expect(decide(to('/users', { perm: 'section.users' }), session([]), sections)).toBe('/waiting')
  })
  it('redirects a missing permission to the first allowed section', () => {
    expect(decide(to('/users', { perm: 'section.users' }), session(['section.sources']), sections)).toBe('/sources')
  })
  it('allows a route whose permission is held', () => {
    expect(decide(to('/users', { perm: 'section.users' }), session(['section.users']), sections)).toBe(true)
  })
  it('superadmin passes every permission check', () => {
    expect(decide(to('/users', { perm: 'section.users' }), session([], { isSuperadmin: true }), sections)).toBe(true)
  })
  it('the root path goes to the first allowed section', () => {
    expect(decide(to('/'), session(['section.sources', 'section.users']), sections)).toBe('/sources')
  })
  it('the waiting page sends an account that has permissions to its section', () => {
    expect(decide(to('/waiting'), session(['section.users']), sections)).toBe('/users')
  })
  it('firstAllowed is null when nothing is allowed', () => {
    expect(firstAllowed(session(['course.edit']), sections)).toBeNull()
  })
})
