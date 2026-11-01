export function sourceCount(passages: number): { key: string; n: number } {
  return passages > 0 ? { key: 'sources.passages.some', n: passages } : { key: 'sources.passages.none', n: 0 }
}
