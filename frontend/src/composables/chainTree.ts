import type { CallTreeNode } from '@/api/types'

export interface ChainIndex {
  byId: Map<string, CallTreeNode>
  parentOf: Map<string, string>
}

export function indexTree(root: CallTreeNode): ChainIndex {
  const byId = new Map<string, CallTreeNode>()
  const parentOf = new Map<string, string>()
  const visit = (node: CallTreeNode): void => {
    byId.set(node.id, node)
    for (const child of node.children) {
      parentOf.set(child.id, node.id)
      visit(child)
    }
  }
  visit(root)
  return { byId, parentOf }
}

/** The ids from the root down to a node, the node included. */
export function pathTo(index: ChainIndex, id: string): string[] {
  const path: string[] = []
  for (let at: string | undefined = id; at; at = index.parentOf.get(at)) path.unshift(at)
  return path
}

const text = (node: CallTreeNode, key: string): string => String(node.facts[key] ?? '').toLowerCase()

/** A block or topic answers a search when its plan id or title contains the query. */
export function answersSearch(node: CallTreeNode, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (q === '') return true
  if (node.kind === 'block') return text(node, 'plan_node_id').includes(q)
  if (node.kind === 'topic') return text(node, 'plan_node_id').includes(q) || node.label.toLowerCase().includes(q)
  return false
}

export interface Shown {
  matches: Set<string>
  visible: Set<string>
  trail: Set<string>
}

/** What a search and the held-only filter leave on screen: each match with its calls, and the path down to it. */
export function filterTree(root: CallTreeNode, index: ChainIndex, query: string, heldOnly: boolean): Shown | null {
  if (query.trim() === '' && !heldOnly) return null
  const matches = new Set<string>()
  const visible = new Set<string>()
  const trail = new Set<string>()
  const keep = (node: CallTreeNode): void => {
    visible.add(node.id)
    node.children.forEach(keep)
  }
  const visit = (node: CallTreeNode): void => {
    const hit = (node.kind === 'block' || node.kind === 'topic') && answersSearch(node, query) && (!heldOnly || (node.kind === 'block' && node.status === 'held'))
    if (hit) {
      matches.add(node.id)
      keep(node)
      for (const id of pathTo(index, node.id).slice(0, -1)) {
        visible.add(id)
        trail.add(id)
      }
      return
    }
    node.children.forEach(visit)
  }
  visit(root)
  return { matches, visible, trail }
}
