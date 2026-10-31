import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(join(__dirname, '../../src/theme/tokens.css'), 'utf8')

function block(selector: string): Record<string, string> {
  const start = css.indexOf(selector)
  const body = css.slice(css.indexOf('{', start) + 1, css.indexOf('}', start))
  return Object.fromEntries([...body.matchAll(/--sb-([\w-]+):\s*(#[0-9a-fA-F]{6})/g)].map((m) => [m[1] as string, m[2] as string]))
}
const light = block(':root {')
const dark = { ...light, ...block(":root[data-theme='dark']") }

function lum(hex: string): number {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
  return 0.2126 * (c[0] as number) + 0.7152 * (c[1] as number) + 0.0722 * (c[2] as number)
}
export function ratio(a: string, b: string): number {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x)
  return ((hi as number) + 0.05) / ((lo as number) + 0.05)
}

const TEXT: Array<[string, string]> = [
  ['fg', 'bg'], ['fg', 'surface'], ['muted', 'bg'], ['muted', 'surface'], ['accent', 'bg'], ['accent', 'surface'],
  ['accent-fg', 'accent'], ['warn-fg', 'warn-bg'], ['danger', 'surface'], ['danger', 'bg'], ['ok', 'surface'], ['ok', 'bg'],
  ['danger-fg', 'danger-bg'], ['ok-fg', 'ok-bg'], ['info-fg', 'info-bg'],
]

for (const [name, tokens] of [['light', light], ['dark', dark]] as const) {
  describe(`${name} theme contrast`, () => {
    for (const [fg, bg] of TEXT) {
      it(`text ${fg} on ${bg} is at least 4.5:1`, () => {
        expect(tokens[fg], `token ${fg}`).toBeTruthy()
        expect(tokens[bg], `token ${bg}`).toBeTruthy()
        expect(ratio(tokens[fg] as string, tokens[bg] as string)).toBeGreaterThanOrEqual(4.5)
      })
    }
    for (const bg of ['bg', 'surface']) {
      it(`focus ring against ${bg} is at least 3:1`, () => {
        expect(ratio(tokens.focus as string, tokens[bg] as string)).toBeGreaterThanOrEqual(3)
      })
    }
    it('large text (headings use fg) keeps at least 3:1 on both surfaces', () => {
      expect(ratio(tokens.fg as string, tokens.bg as string)).toBeGreaterThanOrEqual(3)
    })
  })
}
