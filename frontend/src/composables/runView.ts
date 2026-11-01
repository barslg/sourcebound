export interface RunStep {
  step_key: string
  status: string
  input_digest: string | null
  attempt: number
  error: string | null
}

export interface RunTopic {
  topic_id: string
  title: string
  status: string
}

export interface RunDoc {
  id: string
  status: string
  compiled_dag: Array<Record<string, unknown>>
  dag_digest: string
  estimate: { cost_usd?: string; seconds?: number; blocks?: number; calls?: number; note?: string } | null
  stop_marker: string | null
  stop_code?: string | null
  course_id: string | null
  cost_so_far: string | null
  topics: RunTopic[]
  steps: RunStep[]
}

const WORDS: Record<string, string> = { pending: 'waiting', queued: 'waiting', running: 'writing', checking: 'checking', done: 'ready', held: 'held', failed: 'stopped', stopped: 'stopped' }

export function stepWord(step: RunStep): string {
  return WORDS[step.status] ?? step.status
}

export function stoppedLine(run: Pick<RunDoc, 'status' | 'stop_marker' | 'steps'>): string | null {
  if (run.status !== 'stopped') return null
  if (run.stop_marker) return run.stop_marker
  const failed = run.steps.find((s) => s.status === 'failed')
  return failed ? `${failed.step_key}: ${failed.error ?? failed.status}` : null
}

const TOPIC_WORDS: Record<string, string> = { held: 'held', checking: 'checking', accepted: 'ready', draft: 'waiting' }

export function topicWord(status: string): string {
  return TOPIC_WORDS[status] ?? 'waiting'
}

export function moneyOf(value: string | null | undefined): string | null {
  if (value === null || value === undefined) return null
  const n = Number(value)
  return Number.isFinite(n) ? n.toFixed(2) : null
}

export interface StopBlockRef { topic: string; ordinal: number }

export type StopPlan = {
  modules: Array<{
    topics: Array<{
      title: string
      plan_node_id: string
      blocks: Array<{ plan_node_id: string; brief: string }>
    }>
  }>
} | null

export function stopBlockRef(marker: string | null | undefined, plan: StopPlan): StopBlockRef | null {
  if (!marker || !plan) return null
  const found = /\bblock(?:_\w+)?:([\w-]+)/.exec(marker)
  if (!found) return null
  const node = found[1] as string
  for (const mod of plan.modules) {
    for (const t of mod.topics) {
      const at = t.blocks.findIndex((b) => b.plan_node_id === node)
      if (at >= 0) return { topic: t.title, ordinal: at + 1 }
    }
  }
  return null
}

export interface StepName { role: string; subject: string }

export function stepName(
  stepKey: string,
  plan: { modules: Array<{ topics: Array<{ title: string; plan_node_id: string; blocks: Array<{ plan_node_id: string; brief: string }> }> }> } | null,
): StepName {
  const at = stepKey.indexOf(':')
  const role = at < 0 ? stepKey : stepKey.slice(0, at)
  const node = at < 0 ? '' : stepKey.slice(at + 1)
  const topics = (plan?.modules ?? []).flatMap((m) => m.topics)
  const block = topics.flatMap((t) => t.blocks.map((b) => ({ topic: t.title, ...b }))).find((b) => b.plan_node_id === node)
  if (block) return { role, subject: `${block.topic}: ${block.brief}`.slice(0, 120) }
  const topic = topics.find((t) => t.plan_node_id === node)
  return { role, subject: topic ? topic.title : '' }
}
