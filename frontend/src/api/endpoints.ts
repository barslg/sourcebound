const API = '/api/v1'

export const ENDPOINTS = {
  health: `${API}/health`,
  csrf: `${API}/auth/csrf`,
  me: `${API}/auth/me`,
  logout: `${API}/auth/logout`,
  users: `${API}/users`,
  roles: `${API}/roles`,
  invites: `${API}/invites`,
  operations: `${API}/operations`,
  operationEcho: `${API}/operations/echo`,
  events: `${API}/events`,
  courses: `${API}/courses`,
} as const

export const userBan = (id: string): string => `${ENDPOINTS.users}/${encodeURIComponent(id)}/ban`
export const userItem = (id: string): string => `${ENDPOINTS.users}/${encodeURIComponent(id)}`
export const roleItem = (name: string): string => `${ENDPOINTS.roles}/${encodeURIComponent(name)}`
export const roleAssign = (name: string): string => `${roleItem(name)}/assign`
export const roleRevoke = (name: string): string => `${roleItem(name)}/revoke`
export const operationItem = (id: string): string => `${ENDPOINTS.operations}/${encodeURIComponent(id)}`
export const inviteRevoke = (token: string): string => `${ENDPOINTS.invites}/${encodeURIComponent(token)}/revoke`
export const inviteAccept = (token: string): string => `${ENDPOINTS.invites}/${encodeURIComponent(token)}/accept`

export function loginUrl(next?: string): string {
  const q = new URLSearchParams({ provider: 'google' })
  if (next) q.set('next', next)
  return `${API}/auth/login?${q.toString()}`
}

export const LIVE_ENDPOINTS: readonly string[] = [
  ENDPOINTS.csrf, ENDPOINTS.me, ENDPOINTS.logout, ENDPOINTS.users, ENDPOINTS.roles,
  ENDPOINTS.invites, ENDPOINTS.operationEcho, ENDPOINTS.events,
]

export const PENDING_ENDPOINTS: readonly string[] = [ENDPOINTS.courses]
