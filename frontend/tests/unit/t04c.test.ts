import { describe, expect, it } from 'vitest'
import { ENDPOINTS, notificationRead } from '../../src/api/endpoints'
import { sourceHandles, type PlanBlock } from '../../src/composables/planTree'
import { notificationLabel, unreadOf } from '../../src/composables/notifications'
import { moneyOf, topicWord } from '../../src/composables/runView'
import { sourceCount } from '../../src/composables/sourceView'

describe('run view words', () => {
  it('maps the backend topic states to plain words', () => {
    expect(topicWord('held')).toBe('held')
    expect(topicWord('checking')).toBe('checking')
    expect(topicWord('accepted')).toBe('ready')
    expect(topicWord('draft')).toBe('waiting')
    expect(topicWord('something-new')).toBe('waiting')
  })
  it('formats cost from the ledger and says unknown for missing numbers', () => {
    expect(moneyOf('0.1234')).toBe('0.12')
    expect(moneyOf('1')).toBe('1.00')
    expect(moneyOf(null)).toBeNull()
    expect(moneyOf('not a number')).toBeNull()
  })
})

describe('plan blocks', () => {
  it('reads source handles from source_refs', () => {
    const block = { id: 'b', source_refs: [{ handle: 'P1' }, { handle: 'P2' }] } as unknown as PlanBlock
    expect(sourceHandles(block)).toEqual(['P1', 'P2'])
    expect(sourceHandles({ id: 'x' } as unknown as PlanBlock)).toEqual([])
  })
})

describe('notifications', () => {
  const rows = [
    { id: '1', kind: 'operation.done', payload: { operation_id: 'o1', status: 'done' }, read: false, created_at: null },
    { id: '2', kind: 'operation.done', payload: {}, read: true, created_at: null },
  ]
  it('counts unread rows from the server flag', () => {
    expect(unreadOf(rows)).toBe(1)
  })
  it('keys the label by kind and falls back to the raw kind', () => {
    expect(notificationLabel('operation.done')).toBe('notification.kind.operation_done')
    expect(notificationLabel('weird kind')).toBe('notification.kind.other')
  })
  it('has endpoints for the list and for marking read', () => {
    expect(ENDPOINTS.notifications).toBe('/api/v1/notifications')
    expect(notificationRead('a b')).toBe('/api/v1/notifications/a%20b/read')
  })
})

describe('sources', () => {
  it('counts passages in words, not as a percentage', () => {
    expect(sourceCount(0)).toEqual({ key: 'sources.passages.none', n: 0 })
    expect(sourceCount(3)).toEqual({ key: 'sources.passages.some', n: 3 })
  })
})

import { toWire, type PlanTree } from '../../src/composables/planTree'

describe('plan wire operations', () => {
  const topic = (id: string) => ({ id, kind: 'lesson', title: id, objectives: [], through_line_note: '', plan_node_id: id, blocks: [] })
  const tree: PlanTree = {
    modules: [
      { id: 'm1', title: 'M1', purpose: '', entry_knowledge: '', exit_knowledge: '', topics: [topic('a'), topic('b'), topic('c')] },
      { id: 'm2', title: 'M2', purpose: '', entry_knowledge: '', exit_knowledge: '', topics: [] },
    ],
  }
  it('sends the new ordinal of every sibling that changed place, because the backend sets one ordinal per node', () => {
    expect(toWire(tree, [{ op: 'move_topic', id: 'c', ordinal: 0 }])).toEqual([
      { op: 'move_topic', id: 'c', ordinal: 0 },
      { op: 'move_topic', id: 'a', ordinal: 1 },
      { op: 'move_topic', id: 'b', ordinal: 2 },
    ])
    expect(toWire(tree, [{ op: 'move_module', id: 'm2', ordinal: 0 }])).toEqual([
      { op: 'move_module', id: 'm2', ordinal: 0 },
      { op: 'move_module', id: 'm1', ordinal: 1 },
    ])
  })
  it('passes other operations through and tracks the tree between operations', () => {
    const out = toWire(tree, [
      { op: 'rename_topic', id: 'a', title: 'A2' },
      { op: 'move_topic', id: 'b', ordinal: 0 },
      { op: 'move_topic', id: 'c', ordinal: 1 },
    ])
    expect(out[0]).toEqual({ op: 'rename_topic', id: 'a', title: 'A2' })
    expect(out.slice(1)).toEqual([
      { op: 'move_topic', id: 'b', ordinal: 0 },
      { op: 'move_topic', id: 'a', ordinal: 1 },
      { op: 'move_topic', id: 'c', ordinal: 1 },
      { op: 'move_topic', id: 'a', ordinal: 2 },
    ])
  })
  it('sends nothing for a move that changes no place', () => {
    expect(toWire(tree, [{ op: 'move_topic', id: 'a', ordinal: 0 }])).toEqual([])
  })
})
