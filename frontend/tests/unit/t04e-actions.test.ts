import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import en from '../../src/i18n/locales/en.json'
import nb from '../../src/i18n/locales/nb.json'
import uk from '../../src/i18n/locales/uk.json'
import { ACTION_ICONS } from '../../src/ui/actionIcons'
import { ACTION_IDS, ACTIONS } from '../../src/ui/actionMeta'

function files(dir: string, ext: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name)
    return statSync(p).isDirectory() ? files(p, ext) : p.endsWith(ext) ? [p] : []
  })
}
const SRC = join(__dirname, '../../src')
const vue = files(SRC, '.vue').map((p) => ({ p, text: readFileSync(p, 'utf8') }))

describe('action map', () => {
  it('has one icon per action and one action per icon', () => {
    const icons = ACTIONS.map((x) => ACTION_ICONS[x.id])
    expect(icons.every(Boolean)).toBe(true)
    expect(new Set(icons).size).toBe(ACTIONS.length)
    expect(new Set(ACTION_IDS).size).toBe(ACTIONS.length)
    expect(Object.keys(ACTION_ICONS).sort()).toEqual([...ACTION_IDS].sort())
  })
  it('names every action in all three locales and gives each a place and a kind', () => {
    for (const x of ACTIONS) {
      const key = x.label.replace(/^action\./, '')
      for (const [name, tree] of Object.entries({ en, nb, uk })) {
        expect((tree as unknown as { action: Record<string, string> }).action[key], `${name} ${x.label}`).toBeTruthy()
      }
      expect(x.where.length, x.id).toBeGreaterThan(0)
    }
  })
  it('gives each shortcut to one action only', () => {
    const keys = ACTIONS.flatMap((x) => (x.shortcut ? [x.shortcut] : []))
    expect(new Set(keys).size).toBe(keys.length)
  })
  it('lets every ActionButton in src use only an id from the map', () => {
    const used: string[] = []
    const bad: string[] = []
    for (const f of vue) {
      for (const m of f.text.matchAll(/<ActionButton\b[^>]*>/g)) {
        const id = /\saction="([^"]+)"/.exec(m[0])?.[1]
        if (!id && /\s:action="\w+"/.test(m[0]) && f.p.endsWith('components/ConfirmDialog.vue')) continue
        if (!id) bad.push(`${f.p}: ${m[0].slice(0, 60)}`)
        else if (!ACTION_IDS.includes(id)) bad.push(`${f.p}: unknown action ${id}`)
        else used.push(id)
      }
    }
    expect(bad).toEqual([])
    expect(used.length).toBeGreaterThan(30)
  })
  it('is used: every action id is named by some component outside the map', () => {
    const all = vue.map((f) => f.text).join('\n') + files(SRC, '.ts').filter((p) => !p.includes('/ui/action')).map((p) => readFileSync(p, 'utf8')).join('\n')
    const unused = ACTION_IDS.filter((id) => !new RegExp(`action="${id}"|action: '${id}'|ACTION_ICONS\\.${id}\\b`).test(all))
    expect(unused).toEqual([])
  })
})
