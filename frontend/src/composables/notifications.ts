import type { NotificationRow } from '@/api/types'

export function unreadOf(rows: NotificationRow[]): number {
  return rows.filter((r) => !r.read).length
}

export function notificationLabel(kind: string): string {
  return /^[a-z]+(\.[a-z]+)*$/.test(kind) ? `notification.kind.${kind.replace(/\./g, '_')}` : 'notification.kind.other'
}

export function identityOf(row: NotificationRow): string {
  const op = row.payload.operation_id
  return typeof op === 'string' ? op : row.id
}
