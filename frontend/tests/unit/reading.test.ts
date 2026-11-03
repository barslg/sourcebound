import { describe, expect, it } from 'vitest'
import { renderReading } from '../../src/composables/reading'

const chip = { handle: 'P4', source_number: 2, source_title: 'Docs', address_label: 'Setup, para 3', passage_id: 'pid-4' }
const opts = { chips: [chip], terms: [{ term: 'base_url', definition: 'A base_url is the address.' }], openId: null, openTerm: null }

describe('the learner reading renderer', () => {
  it('renders paragraphs, bold, lists and a GFM table', () => {
    const html = renderReading('First **bold** line.\n\nSecond.\n\n- a\n- b\n\n| A | B |\n|---|---|\n| 1 | 2 |', opts)
    expect(html.match(/<p>/g)).toHaveLength(2)
    expect(html).toContain('<strong>bold</strong>')
    expect(html).toContain('<li>a</li>')
    expect(html).toMatch(/<table>[\s\S]*<td>1<\/td>/)
  })
  it('turns a passage marker into a numbered citation button and keeps an unknown handle as muted text', () => {
    const html = renderReading('⟦P4⟧ The client sends. ⟦P9⟧ Unknown.', opts)
    expect(html).toContain('<button type="button" class="cite-mark" data-handle="P4" data-handle-chip aria-controls="companion-panel" aria-expanded="false" title="Docs · Setup, para 3">2</button>')
    expect(html).toContain('<span class="cite-mark muted" data-handle-chip>P9</span>')
    expect(html).not.toContain('⟦')
  })
  it('marks the first occurrence of a term only, never inside code', () => {
    const html = renderReading('Set `base_url` first. The base_url is the address; a base_url never changes.', opts)
    expect(html.match(/class="term-mark"/g)).toHaveLength(1)
    expect(html).toContain('<code>base_url</code>')
    expect(html).toContain('data-term="base_url" aria-controls="companion-panel" aria-expanded="false" title="A base_url is the address.">base_url</button>')
  })
  it('never interprets HTML from the body and escapes what it writes into attributes', () => {
    const html = renderReading('<img src=x onerror=alert(1)> text ⟦P4⟧', { ...opts, chips: [{ ...chip, source_title: 'A "quoted" <b>' }] })
    expect(html).not.toContain('<img')
    expect(html).toContain('&lt;img')
    expect(html).toContain('title="A &quot;quoted&quot; &lt;b&gt; · Setup, para 3"')
  })
})
