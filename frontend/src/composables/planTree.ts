export interface PlanBlock { id: string; plan_node_id: string; role: string; block_type: string; brief: string; status: string; hold_reason: string | null; source_refs?: Array<{ handle: string; passage_id?: string | null; source_id?: string | null; address_label?: string | null }> }
export function sourceHandles(block: PlanBlock): string[] {
  return (block.source_refs ?? []).map((r) => r.handle)
}

export interface PlanTopic { id: string; kind: string; title: string; objectives: string[]; through_line_note: string; plan_node_id: string; blocks: PlanBlock[] }
export interface PlanModule { id: string; title: string; purpose: string; entry_knowledge: string; exit_knowledge: string; topics: PlanTopic[] }
export interface PlanTree { modules: PlanModule[]; row_version?: number; uncovered?: Array<{ handle: string; reason: string; address?: string; excerpt?: string }>; through_line?: { case?: string; handoffs?: Array<{ topic_plan_node_id: string; note: string }> } | null }

export interface PlanNode {
  key: string
  label: string
  data: { kind: 'module' | 'topic'; id: string }
  children?: PlanNode[]
  draggable: boolean
  droppable: boolean
}

export function toNodes(tree: PlanTree): PlanNode[] {
  return tree.modules.map((m) => ({
    key: m.id,
    label: m.title,
    data: { kind: 'module', id: m.id },
    draggable: true,
    droppable: true,
    children: m.topics.map((t) => ({ key: t.id, label: t.title, data: { kind: 'topic', id: t.id }, draggable: true, droppable: true })),
  }))
}

export interface MoveOp { op: 'move_module' | 'move_topic'; id: string; ordinal: number }

export function moveTarget(tree: PlanTree, dragId: string, dropId: string): MoveOp | null {
  if (dragId === dropId) return null
  const mi = tree.modules.findIndex((m) => m.id === dragId)
  if (mi >= 0) {
    const to = tree.modules.findIndex((m) => m.id === dropId)
    return to >= 0 ? { op: 'move_module', id: dragId, ordinal: to } : null
  }
  for (const m of tree.modules) {
    if (m.topics.some((t) => t.id === dragId)) {
      const to = m.topics.findIndex((t) => t.id === dropId)
      return to >= 0 ? { op: 'move_topic', id: dragId, ordinal: to } : null
    }
  }
  return null
}

export function findTopic(tree: PlanTree, id: string): PlanTopic | null {
  for (const m of tree.modules) {
    const t = m.topics.find((x) => x.id === id)
    if (t) return t
  }
  return null
}

export function titleOf(tree: PlanTree, id: string): string {
  return tree.modules.find((m) => m.id === id)?.title ?? findTopic(tree, id)?.title ?? id
}

export type PlanOp =
  | { op: 'rename_module' | 'rename_topic'; id: string; title: string }
  | { op: 'delete_module' | 'delete_topic'; id: string }
  | MoveOp
  | { op: 'set_constraint'; key: string; answer: Record<string, unknown> }
  | { op: 'add_module'; title: string }
  | { op: 'add_topic'; module_id: string; title: string; kind?: string }

function reorder<T extends { id: string }>(items: T[], id: string, ordinal: number): T[] {
  const from = items.findIndex((x) => x.id === id)
  if (from < 0) return items
  const next = [...items]
  const [moved] = next.splice(from, 1)
  next.splice(Math.min(ordinal, next.length), 0, moved as T)
  return next
}

export function applyOps(tree: PlanTree, ops: PlanOp[]): PlanTree {
  let modules: PlanModule[] = tree.modules.map((m) => ({ ...m, topics: m.topics.map((t) => ({ ...t })) }))
  for (const op of ops) {
    if (op.op === 'rename_module') modules = modules.map((m) => (m.id === op.id ? { ...m, title: op.title } : m))
    else if (op.op === 'rename_topic') modules = modules.map((m) => ({ ...m, topics: m.topics.map((t) => (t.id === op.id ? { ...t, title: op.title } : t)) }))
    else if (op.op === 'delete_module') modules = modules.filter((m) => m.id !== op.id)
    else if (op.op === 'delete_topic') modules = modules.map((m) => ({ ...m, topics: m.topics.filter((t) => t.id !== op.id) }))
    else if (op.op === 'move_module') modules = reorder(modules, op.id, op.ordinal)
    else if (op.op === 'move_topic') modules = modules.map((m) => ({ ...m, topics: reorder(m.topics, op.id, op.ordinal) }))
  }
  return { modules }
}

function siblingsOf(tree: PlanTree, op: MoveOp): Array<{ id: string }> {
  if (op.op === 'move_module') return tree.modules
  return tree.modules.find((m) => m.topics.some((t) => t.id === op.id))?.topics ?? []
}

export function toWire(base: PlanTree, ops: PlanOp[]): PlanOp[] {
  const out: PlanOp[] = []
  let current = base
  for (const op of ops) {
    const next = applyOps(current, [op])
    if (op.op === 'move_module' || op.op === 'move_topic') {
      const before = siblingsOf(current, op).map((x) => x.id)
      const after = siblingsOf(next, op).map((x) => x.id)
      after.forEach((id, ordinal) => {
        if (before.indexOf(id) !== ordinal) out.push({ op: op.op, id, ordinal })
      })
    } else {
      out.push(op)
    }
    current = next
  }
  return out
}
