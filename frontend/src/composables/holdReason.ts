const RUBRIC = new Set(['task_fit', 'grounding', 'continuity', 'answer_key', 'audience_language', 'planted_error', 'coverage', 'block_type'])

/** The checker's hold text with each rubric item id replaced by its plain name; unknown text passes through unchanged. */
export function holdText(reason: string | null, name: (id: string) => string): string {
  if (!reason) return ''
  return reason
    .split('; ')
    .map((part) => {
      const at = part.indexOf(': ')
      const id = at > 0 ? part.slice(0, at) : ''
      return RUBRIC.has(id) ? `${name(id)}: ${part.slice(at + 2)}` : part
    })
    .join('; ')
}
