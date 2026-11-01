import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { ERROR_KINDS } from '../../src/composables/learnAnswer'
import en from '../../src/i18n/locales/en.json'
import nb from '../../src/i18n/locales/nb.json'
import uk from '../../src/i18n/locales/uk.json'

const backend = readFileSync(join(__dirname, '../../../src/app/course/errorspot.py'), 'utf8')

describe('learner surface mirrors the backend enums (read from the backend file, no copy)', () => {
  it('lists exactly the error kinds the grader knows', () => {
    const block = /MUTATION_KINDS: tuple\[str, \.\.\.\] = \(([^)]*)\)/.exec(backend)?.[1] ?? ''
    const kinds = [...block.matchAll(/"([a-z_]+)"/g)].map((m) => m[1])
    expect(kinds.length).toBeGreaterThan(3)
    expect([...ERROR_KINDS]).toEqual(kinds)
  })
  it('labels every kind, and the unspecified bucket, in en, nb and uk', () => {
    for (const tree of [en, nb, uk]) for (const k of [...ERROR_KINDS, 'unspecified']) expect((tree.learn.kind as Record<string, string>)[k], k).toBeTruthy()
  })
})
