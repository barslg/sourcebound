import { i18n } from '@/i18n'
import { useSessionStore } from '@/stores/session'
import { reasonKey } from './labels'

export function lacking(code: string): string | null {
  const session = useSessionStore()
  return session.can(code) ? null : i18n.global.t(reasonKey(code))
}

export function firstReason(...reasons: Array<string | null | false | undefined>): string | null {
  return reasons.find((r): r is string => typeof r === 'string' && r.length > 0) ?? null
}
