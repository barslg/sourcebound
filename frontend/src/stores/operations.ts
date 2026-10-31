import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { request } from '@/api/client'
import { ENDPOINTS, notificationRead, operationItem } from '@/api/endpoints'
import type { BusMessage } from '@/api/events'
import type { NotificationRow, Operation } from '@/api/types'
import { identityOf } from '@/composables/notifications'
import { notify } from '@/composables/notify'
import { i18n } from '@/i18n'

const TERMINAL = new Set(['done', 'succeeded', 'failed', 'error', 'cancelled'])

export const useOperationsStore = defineStore('operations', () => {
  const items = ref<Record<string, Operation>>({})
  const order = ref<string[]>([])
  const unread = ref<Set<string>>(new Set())
  const failure = ref<string | null>(null)
  const notifications = ref<NotificationRow[]>([])

  const list = computed(() => order.value.map((id) => items.value[id]).filter((o): o is Operation => o !== undefined))
  const unreadCount = computed(() => new Set([...unread.value, ...notifications.value.filter((n) => !n.read).map(identityOf)]).size)

  async function refresh(id: string): Promise<void> {
    try {
      const next = await request<Operation>(operationItem(id))
      const before = items.value[id]
      items.value = { ...items.value, [id]: next }
      if (!order.value.includes(id)) order.value = [id, ...order.value]
      failure.value = null
      const t = i18n.global.t
      if (TERMINAL.has(next.status) && before?.status !== next.status) {
        unread.value = new Set(unread.value).add(id)
        void loadNotifications()
        if (next.status === 'failed' || next.status === 'error') notify('error', t('op.failedToast', { message: next.error ?? next.status }))
        else notify('success', t('op.doneToast'))
      }
    } catch (cause) {
      failure.value = cause instanceof Error ? cause.message : String(cause)
    }
  }

  async function loadNotifications(): Promise<void> {
    try {
      notifications.value = await request<NotificationRow[]>(ENDPOINTS.notifications)
      failure.value = null
    } catch (cause) {
      failure.value = cause instanceof Error ? cause.message : String(cause)
    }
  }

  async function markNotificationsRead(): Promise<void> {
    const open = notifications.value.filter((n) => !n.read)
    try {
      for (const row of open) await request(notificationRead(row.id), { method: 'POST' })
    } catch (cause) {
      failure.value = cause instanceof Error ? cause.message : String(cause)
    }
    if (open.length > 0) await loadNotifications()
  }

  function register(id: string): void {
    if (!order.value.includes(id)) order.value = [id, ...order.value]
    unread.value = new Set(unread.value).add(id)
    notify('info', i18n.global.t('op.queuedToast'))
    void refresh(id)
  }

  function onBus(message: BusMessage): void {
    if (message.operation_id) void refresh(message.operation_id)
  }

  function refetchOpen(): void {
    for (const id of order.value) {
      const op = items.value[id]
      if (!op || !TERMINAL.has(op.status)) void refresh(id)
    }
  }

  function markAllRead(): void {
    unread.value = new Set()
    void markNotificationsRead()
  }

  function reset(): void {
    items.value = {}
    order.value = []
    unread.value = new Set()
    notifications.value = []
  }

  async function submitEcho(message: string): Promise<void> {
    await request(ENDPOINTS.operationEcho, { method: 'POST', body: { message } })
  }

  return { items, order, list, unread, unreadCount, failure, notifications, loadNotifications, markNotificationsRead, refresh, register, onBus, refetchOpen, markAllRead, reset, submitEcho }
})

export function isTerminal(status: string): boolean {
  return TERMINAL.has(status)
}
