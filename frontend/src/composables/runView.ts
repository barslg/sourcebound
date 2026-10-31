export interface RunStep {
  step_key: string
  status: string
  input_digest: string | null
  attempt: number
  error: string | null
}

export interface RunDoc {
  id: string
  status: string
  compiled_dag: Array<Record<string, unknown>>
  dag_digest: string
  estimate: { cost_usd?: string; seconds?: number; blocks?: number; calls?: number; note?: string } | null
  stop_marker: string | null
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
