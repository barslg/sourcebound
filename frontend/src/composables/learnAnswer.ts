import type { Chip, KindCounts } from '@/api/types'

/** Mirror of MUTATION_KINDS in src/app/course/errorspot.py; the unit test t06-parity reads the backend file and fails on drift. */
export const ERROR_KINDS = ['number_date', 'negation', 'scope', 'entity_swap', 'cause_reversal', 'bad_citation'] as const
/** The four risk tiers the brief's scenario exercise sorts into; the backend content carries no tier list. */
export const TIERS = ['unacceptable', 'high-risk', 'limited', 'minimal'] as const
/** Mirror of ASSESSED_TYPES in src/app/services/learner.py; the unit test t06-parity reads the backend file and fails on drift. */
export const ANSWERABLE = ['choice', 'matching', 'cloze', 'ordering', 'scenario_practice', 'spot_the_error'] as const
export const BLANK = '___'

export interface Draft {
  picked: number | null
  blanks: string[]
  mapping: Record<string, string>
  /** Served step ids in the learner's order; empty until a step was moved. */
  order: string[]
  labels: Record<string, string>
  passages: Record<string, string>
  sentence: string
  kind: string
  passage: string
  corrected: string
}

export const isAnswerable = (type: string): boolean => (ANSWERABLE as readonly string[]).includes(type)
export const blankCount = (text: string): number => text.split(BLANK).length - 1

export function emptyDraft(type: string, content: Record<string, unknown>): Draft {
  const text = typeof content.text === 'string' ? content.text : ''
  return { picked: null, blanks: type === 'cloze' ? Array.from({ length: blankCount(text) }, () => '') : [], mapping: {}, order: [], labels: {}, passages: {}, sentence: '', kind: '', passage: '', corrected: '' }
}

/** True when the learner has given the block anything to grade. */
export function answered(type: string, d: Draft): boolean {
  if (type === 'choice') return d.picked !== null
  if (type === 'cloze') return d.blanks.some((b) => b.trim() !== '')
  if (type === 'matching') return Object.keys(d.mapping).length > 0
  if (type === 'ordering') return d.order.length > 0
  if (type === 'scenario_practice') return Object.keys(d.labels).length > 0
  if (type === 'spot_the_error') return d.sentence !== ''
  return false
}

/** The body of POST /learn/blocks/{id}/answer; null when nothing was answered. */
export function payloadOf(type: string, d: Draft): Record<string, unknown> | null {
  if (!answered(type, d)) return null
  if (type === 'choice') return { picked: d.picked }
  if (type === 'cloze') return { blanks: d.blanks }
  if (type === 'matching') return { mapping: d.mapping }
  if (type === 'ordering') return { order: d.order }
  if (type === 'scenario_practice') return { labels: d.labels, passages: d.passages }
  return { sentence_id: d.sentence, kind: d.sentence === 'none' ? '' : d.kind, passage_id: d.sentence === 'none' ? '' : d.passage, corrected_value: d.corrected.trim() }
}

/** The body of POST /learn/topics/{id}/submit: only the blocks that were answered. */
export function pageAnswers(blocks: Array<{ id: string; block_type: string }>, drafts: Record<string, Draft>): Record<string, Record<string, unknown>> {
  const out: Record<string, Record<string, unknown>> = {}
  for (const b of blocks) {
    const draft = drafts[b.id]
    const body = draft ? payloadOf(b.block_type, draft) : null
    if (body) out[b.id] = body
  }
  return out
}

export const chipKey = (c: Chip): string => `${c.passage_id}:${c.handle}`
export const total = (c: KindCounts): number => c.hits + c.false_alarms + c.misses
