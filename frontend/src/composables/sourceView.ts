export function sourceCount(passages: number): { key: string; n: number } {
  return passages > 0 ? { key: 'sources.passages.some', n: passages } : { key: 'sources.passages.none', n: 0 }
}

/** A passage address for people: heading chain without the stored `h:`/`¶` markers, `, para N` at the end. */
export function prettyAddress(raw: string | null | undefined): string {
  return (raw ?? '').replace(/^h:/, '').replace(/¶\s*>\s*/g, ' › ').replace(/¶?\s*\/para(\d+)/, ', para $1').replace(/¶/g, '').trim()
}
