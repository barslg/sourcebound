import { i18n } from '@/i18n'
import type { TranslationRow } from '@/api/types'

export type TranslationState = 'ready' | 'held' | 'partial' | 'failed'

export function translationState(row: TranslationRow): TranslationState {
  if (row.status === 'ready') return 'ready'
  if (row.status === 'failed') return 'failed'
  return row.held > 0 ? 'held' : 'partial'
}

export function translationTone(row: TranslationRow): 'ok' | 'warn' | 'danger' {
  const state = translationState(row)
  return state === 'ready' ? 'ok' : state === 'failed' ? 'danger' : 'warn'
}

export function translationLabel(row: TranslationRow): string {
  const t = i18n.global.t
  const state = translationState(row)
  const parts = [`${t(`language.${row.language}`, row.language)}`, t(`translate.state.${state}`)]
  if (row.held > 0) parts.push(t('translate.heldCount', { count: t('count.block', row.held) }))
  if (row.failed > 0) parts.push(t('translate.failedCount', { count: t('count.block', row.failed) }))
  return parts.join(' · ')
}
