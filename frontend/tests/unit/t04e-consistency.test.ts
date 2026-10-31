import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name)
    return statSync(p).isDirectory() ? files(p) : /\.(vue|ts)$/.test(p) ? [p] : []
  })
}
const SRC = join(__dirname, '../../src')
const all = files(SRC).map((p) => ({ rel: relative(SRC, p), text: readFileSync(p, 'utf8') }))

function offenders(pattern: RegExp, allowed: string[]): string[] {
  return all.filter((f) => pattern.test(f.text) && !allowed.includes(f.rel)).map((f) => f.rel)
}

describe('one component per job', () => {
  it('has one toast helper and one toast region', () => {
    expect(offenders(/useToast\(/, ['App.vue'])).toEqual([])
    expect(offenders(/<Toast\b/, ['App.vue'])).toEqual([])
    expect(offenders(/ToastService(?!Methods)/, ['main.ts'])).toEqual([])
  })
  it('has one breadcrumb implementation', () => {
    expect(offenders(/class="crumbs"/, ['components/Breadcrumbs.vue'])).toEqual([])
  })
  it('wraps every PrimeVue primitive that has a job component', () => {
    expect(offenders(/from 'primevue\/button'/, ['components/ActionButton.vue'])).toEqual([])
    expect(offenders(/from 'primevue\/dialog'/, ['components/AppDialog.vue'])).toEqual([])
    expect(offenders(/from 'primevue\/drawer'/, ['components/AppDrawer.vue'])).toEqual([])
    expect(offenders(/from 'primevue\/datatable'/, ['components/AppTable.vue', 'views/UsersView.vue'])).toEqual([])
  })
  it('keeps old duplicates deleted', () => {
    expect(offenders(/GuardedButton|SectionHeading/, [])).toEqual([])
  })
  it('lays every field out through FormField', () => {
    expect(offenders(/class="field\b/, ['components/FormField.vue'])).toEqual([])
  })
  it('uses one icon set: no primeicons classes', () => {
    expect(offenders(/\bpi pi-/, [])).toEqual([])
  })
  it('draws status chips with StatusChip, never a raw warn chip', () => {
    expect(offenders(/chip chip-warn|class="chip"[^>]*data-testid="(source|run)-state/, [])).toEqual([])
  })
  it('draws every queued state with the one Queued component, never a chip or a line of its own', () => {
    expect(offenders(/data-testid="queued-line"/, ['components/Queued.vue'])).toEqual([])
    expect(offenders(/<StatusChip[^>]*op\.queuedToast|<p[^>]*class="chip"[^>]*>\{\{ t\('op\.queuedToast/, [])).toEqual([])
    const users = all.filter((f) => /<Queued\b/.test(f.text)).map((f) => f.rel).sort()
    expect(users).toEqual(['components/BellMenu.vue', 'views/course/FixView.vue', 'views/course/PlanView.vue', 'views/course/PublishView.vue'])
  })
  it('lists the Queued component in the grammar file', () => {
    expect(readFileSync(join(__dirname, '../../../docs/ui/GRAMMAR.md'), 'utf8')).toMatch(/`components\/Queued\.vue`/)
  })
  it('lets only ScopePicker choose scope: no free-text tags input', () => {
    expect(offenders(/parseTags\(|scopeText|queriesText.*tag/, [])).toEqual([])
  })
})
