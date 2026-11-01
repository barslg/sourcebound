import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import en from '../../src/i18n/locales/en.json'
import nb from '../../src/i18n/locales/nb.json'
import uk from '../../src/i18n/locales/uk.json'

type Tree = { [k: string]: string | Tree }
function flat(tree: Tree, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(tree)) {
    const key = prefix ? `${prefix}.${k}` : k
    if (typeof v === 'string') out[key] = v
    else Object.assign(out, flat(v, key))
  }
  return out
}
const placeholders = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()
const locales = { en: flat(en as Tree), nb: flat(nb as Tree), uk: flat(uk as Tree) }

describe('i18n catalogues', () => {
  it('have identical key sets', () => {
    const base = Object.keys(locales.en).sort()
    expect(Object.keys(locales.nb).sort()).toEqual(base)
    expect(Object.keys(locales.uk).sort()).toEqual(base)
  })
  it('keep the same placeholders per key', () => {
    for (const [key, text] of Object.entries(locales.en)) {
      expect(placeholders(locales.nb[key] ?? ''), key).toEqual(placeholders(text))
      expect(placeholders(locales.uk[key] ?? ''), key).toEqual(placeholders(text))
    }
  })
  it('have no empty value, emoji or arrow', () => {
    for (const [name, map] of Object.entries(locales)) {
      for (const [key, text] of Object.entries(map)) {
        expect(text.trim().length, `${name}:${key}`).toBeGreaterThan(0)
        expect(/[\p{Extended_Pictographic}←-⇿⟵-⟹⤀-⥿]/u.test(text), `${name}:${key}`).toBe(false)
      }
    }
  })
  it('define every key that the source uses as a literal', () => {
    const used = new Set<string>()
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const p = join(dir, name)
        if (statSync(p).isDirectory()) walk(p)
        else if (/\.(vue|ts)$/.test(name)) {
          for (const m of readFileSync(p, 'utf8').matchAll(/\bt\(\s*'([a-z][\w.]*)'/g)) used.add(m[1] as string)
        }
      }
    }
    walk(join(__dirname, '../../src'))
    const missing = [...used].filter((k) => !(k in locales.en))
    expect(missing).toEqual([])
  })
})
