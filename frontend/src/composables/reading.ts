import MarkdownIt from 'markdown-it'
import type { Chip, Term } from '@/api/types'

/** The learner's reading: the writer's markdown (paragraphs, lists, tables, code, bold) with a
 *  numbered citation mark at every passage boundary and the topic's terms marked once each.
 *  HTML in the source is never interpreted (html: false), so the output is safe to mount. */
export interface ReadingOptions { chips: Chip[]; terms: Term[]; openId: string | null; openTerm: string | null }

const MARKER = /⟦(P[1-9][0-9]*)⟧/g
const md = new MarkdownIt({ html: false, linkify: false, typographer: false })
md.disable(['image'])

type Env = ReadingOptions & { seen: Set<string> }
type Tok = ReturnType<MarkdownIt['parse']>[number]

function termAt(text: string, env: Env): { index: number; length: number; term: Term } | null {
  let best: { index: number; length: number; term: Term } | null = null
  for (const term of env.terms) {
    if (env.seen.has(term.term) || term.term.trim() === '') continue
    const re = new RegExp(`(?<![\\p{L}\\p{N}_])${term.term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![\\p{L}\\p{N}_])`, 'iu')
    const m = re.exec(text)
    if (m && (best === null || m.index < best.index)) best = { index: m.index, length: m[0].length, term }
  }
  return best
}

function pieces(text: string, env: Env, make: (type: string, content: string, meta?: Record<string, string>) => Tok): Tok[] {
  const out: Tok[] = []
  let rest = text
  while (rest !== '') {
    const found = termAt(rest, env)
    if (!found) { out.push(make('text', rest)); break }
    if (found.index > 0) out.push(make('text', rest.slice(0, found.index)))
    env.seen.add(found.term.term)
    out.push(make('term', rest.slice(found.index, found.index + found.length), { term: found.term.term, definition: found.term.definition }))
    rest = rest.slice(found.index + found.length)
  }
  return out
}

md.core.ruler.push('sourcebound', (state) => {
  const env = state.env as Env
  for (const token of state.tokens) {
    if (token.type !== 'inline' || !token.children) continue
    const make = (type: string, content: string, meta?: Record<string, string>): Tok => {
      const t = new state.Token(type, '', 0)
      t.content = content
      if (meta) t.meta = meta
      return t
    }
    const children: Tok[] = []
    for (const child of token.children) {
      if (child.type !== 'text') { children.push(child); continue }
      let last = 0
      for (const m of child.content.matchAll(MARKER)) {
        if (m.index > last) children.push(...pieces(child.content.slice(last, m.index), env, make))
        children.push(make('cite', m[1] as string))
        last = m.index + m[0].length
      }
      if (last < child.content.length) children.push(...pieces(child.content.slice(last), env, make))
    }
    token.children = children
  }
})

const esc = (s: string): string => md.utils.escapeHtml(s)

md.renderer.rules.cite = (tokens, idx, _o, env: Env) => {
  const handle = tokens[idx]?.content ?? ''
  const chip = env.chips.find((c) => c.handle === handle)
  if (!chip) return `<span class="cite-mark muted" data-handle-chip>${esc(handle)}</span>`
  const label = chip.source_number > 0 ? String(chip.source_number) : handle
  const title = `${chip.source_title} · ${chip.address_label}`
  return `<button type="button" class="cite-mark" data-handle="${esc(handle)}" data-handle-chip aria-controls="companion-panel" aria-expanded="${env.openId === chip.passage_id}" title="${esc(title)}">${esc(label)}</button>`
}

md.renderer.rules.term = (tokens, idx, _o, env: Env) => {
  const t = tokens[idx] as Tok
  const term = String(t.meta?.term ?? '')
  const definition = String(t.meta?.definition ?? '')
  return `<button type="button" class="term-mark" data-term="${esc(term)}" aria-controls="companion-panel" aria-expanded="${env.openTerm === term}" title="${esc(definition)}">${esc(t.content)}</button>`
}

export function renderReading(body: string, options: ReadingOptions): string {
  const env: Env = { ...options, seen: new Set<string>() }
  return md.render(body, env)
}

/** Plain text of a reading (the reader's chatter checks and the tutor's quotes compare against it). */
export function plainText(body: string): string {
  return body.replace(MARKER, '').replace(/\*\*|`/g, '')
}

/** An option the writer gave as code or console output: more than one line, or a prompt line; shown with its line breaks in a monospace face. */
export function looksLikeCode(text: string): boolean {
  return text.includes('\n') || /^(>>>|\.\.\.)/.test(text.trimStart())
}
