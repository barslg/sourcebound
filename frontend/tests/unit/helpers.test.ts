import { describe, expect, it } from 'vitest'
import { needsNow, type CourseFacts } from '../../src/composables/needsNow'
import { applyOps, moveTarget, toNodes, type PlanTree } from '../../src/composables/planTree'
import { stepWord, stoppedLine, type RunStep } from '../../src/composables/runView'

const base: CourseFacts = { status: 'draft', profile: false, plan: false, openQuestions: 0, published: false }

describe('needsNow', () => {
  it('names one next action in order', () => {
    expect(needsNow(base)).toBe('needs.profile')
    expect(needsNow({ ...base, profile: true })).toBe('needs.plan')
    expect(needsNow({ ...base, profile: true, plan: true, openQuestions: 2 })).toBe('needs.questions')
    expect(needsNow({ ...base, profile: true, plan: true })).toBe('needs.generate')
    expect(needsNow({ ...base, profile: true, plan: true, status: 'generated' })).toBe('needs.publish')
    expect(needsNow({ ...base, published: true, status: 'published' })).toBe('needs.nothing')
  })
})

describe('run view', () => {
  it('maps a step status to a word and keeps unknown statuses visible', () => {
    const word = (status: string) => stepWord({ step_key: 'k', status, input_digest: null, attempt: 1, error: null })
    expect(word('pending')).toBe('waiting')
    expect(word('running')).toBe('writing')
    expect(word('done')).toBe('ready')
    expect(word('failed')).toBe('stopped')
    expect(word('weird')).toBe('weird')
  })
  it('says why a stopped run stopped from the marker, else from the failed step', () => {
    const failed: RunStep = { step_key: 'architect:plan', status: 'failed', input_digest: null, attempt: 2, error: 'quota' }
    expect(stoppedLine({ status: 'stopped', stop_marker: 'retry policy exhausted', steps: [failed] })).toBe('retry policy exhausted')
    expect(stoppedLine({ status: 'stopped', stop_marker: null, steps: [failed] })).toBe('architect:plan: quota')
    expect(stoppedLine({ status: 'running', stop_marker: null, steps: [failed] })).toBeNull()
  })
})

describe('plan tree', () => {
  const tree: PlanTree = {
    modules: [
      { id: 'm1', title: 'M1', purpose: '', entry_knowledge: '', exit_knowledge: '', topics: [
        { id: 't1', kind: 'lesson', title: 'T1', objectives: ['o'], through_line_note: '', plan_node_id: 'n1', blocks: [] },
        { id: 't2', kind: 'lesson', title: 'T2', objectives: ['o'], through_line_note: '', plan_node_id: 'n2', blocks: [] },
      ] },
      { id: 'm2', title: 'M2', purpose: '', entry_knowledge: '', exit_knowledge: '', topics: [] },
    ],
  }
  it('builds nodes keyed by id with the kind', () => {
    const nodes = toNodes(tree)
    expect(nodes.map((n) => n.key)).toEqual(['m1', 'm2'])
    expect(nodes[0]?.children?.map((n) => n.key)).toEqual(['t1', 't2'])
    expect(nodes[0]?.data.kind).toBe('module')
    expect(nodes[0]?.children?.[1]?.data.kind).toBe('topic')
  })
  it('turns a drop into a move operation with the destination ordinal, topics stay in their module', () => {
    expect(moveTarget(tree, 't1', 't2')).toEqual({ op: 'move_topic', id: 't1', ordinal: 1 })
    expect(moveTarget(tree, 'm1', 'm2')).toEqual({ op: 'move_module', id: 'm1', ordinal: 1 })
    expect(moveTarget(tree, 't1', 'm2')).toBeNull()
    expect(moveTarget(tree, 't1', 't1')).toBeNull()
  })
})

describe('applyOps', () => {
  const tree: PlanTree = {
    modules: [
      { id: 'm1', title: 'M1', purpose: '', entry_knowledge: '', exit_knowledge: '', topics: [
        { id: 't1', kind: 'lesson', title: 'T1', objectives: ['o'], through_line_note: '', plan_node_id: 'n1', blocks: [] },
        { id: 't2', kind: 'lesson', title: 'T2', objectives: ['o'], through_line_note: '', plan_node_id: 'n2', blocks: [] },
      ] },
      { id: 'm2', title: 'M2', purpose: '', entry_knowledge: '', exit_knowledge: '', topics: [] },
    ],
  }
  it('applies rename, move and delete without touching the input', () => {
    const out = applyOps(tree, [
      { op: 'rename_topic', id: 't1', title: 'Renamed' },
      { op: 'move_topic', id: 't1', ordinal: 1 },
      { op: 'delete_module', id: 'm2' },
    ])
    expect(out.modules.map((m) => m.id)).toEqual(['m1'])
    expect(out.modules[0]?.topics.map((t) => `${t.id}:${t.title}`)).toEqual(['t2:T2', 't1:Renamed'])
    expect(tree.modules).toHaveLength(2)
    expect(tree.modules[0]?.topics[0]?.title).toBe('T1')
  })
})
