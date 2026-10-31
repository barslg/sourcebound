import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'
import { PERMISSION_CODES, ROUTE_CODES } from '../../src/api/permissions.generated'
import { ROWS } from '../../src/router/registry'

const SRC = join(__dirname, '../../src')
function files(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n)
    return statSync(p).isDirectory() ? files(p) : /\.(ts|vue)$/.test(n) && !n.includes('generated') ? [p] : []
  })
}
const enforced = new Set(Object.values(ROUTE_CODES).flat())
const used: Array<{ code: string; where: string }> = []
for (const p of files(SRC)) {
  const text = readFileSync(p, 'utf8')
  for (const m of text.matchAll(/\b(?:lacking|can)\(\s*'([a-z]+\.[a-z]+)'/g)) used.push({ code: m[1] as string, where: relative(SRC, p) })
}
for (const r of ROWS) if (r.access.kind === 'perm') used.push({ code: r.access.code, where: `registry ${r.name}` })

describe('client permission codes come from the backend', () => {
  it('finds the codes the client uses (the scan is not empty)', () => {
    expect(used.length).toBeGreaterThan(20)
  })
  it('every code the client checks is a catalogue code', () => {
    expect(used.filter((u) => !PERMISSION_CODES.includes(u.code))).toEqual([])
  })
  it('every code the client checks is required by a backend route (a section.* code gates nothing)', () => {
    expect(used.filter((u) => !enforced.has(u.code))).toEqual([])
  })
})
