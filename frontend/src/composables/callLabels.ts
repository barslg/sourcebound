import { useI18n } from 'vue-i18n'

interface Usage { tokens_input: number; tokens_cached: number; tokens_output: number; tokens_reasoning: number }
interface Result { status: string; cached_reply: boolean }

/** The words every screen uses for a model call: its step, its result and its token line. */
export function useCallLabels(): {
  agentName: (agent: string) => string
  statusLabel: (row: Result) => string
  statusTone: (row: Result) => 'ok' | 'info' | 'warn'
  tokens: (row: Usage) => string
} {
  const { t, te } = useI18n()
  const agentName = (agent: string): string => (te(`run.role.${agent}`) ? t(`run.role.${agent}`) : te(`calls.agent.${agent}`) ? t(`calls.agent.${agent}`) : agent)
  const statusLabel = (row: Result): string => (row.cached_reply ? t('calls.status.replayed') : te(`calls.status.${row.status}`) ? t(`calls.status.${row.status}`) : row.status)
  const statusTone = (row: Result): 'ok' | 'info' | 'warn' => (row.status === 'completed' ? 'ok' : row.status === 'pending' ? 'info' : 'warn')
  const tokens = (row: Usage): string => t('calls.tokensLine', { input: row.tokens_input, cached: row.tokens_cached, output: row.tokens_output + row.tokens_reasoning })
  return { agentName, statusLabel, statusTone, tokens }
}
