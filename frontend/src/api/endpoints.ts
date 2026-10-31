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
  sources: `${API}/sources`,
  sourceUpload: `${API}/sources/upload`,
  sourceFetch: `${API}/sources/fetch`,
  notifications: `${API}/notifications`,
  permissions: `${API}/permissions`,
  tags: `${API}/tags`,
} as const

export const userBan = (id: string): string => `${ENDPOINTS.users}/${encodeURIComponent(id)}/ban`
export const userUnban = (id: string): string => `${ENDPOINTS.users}/${encodeURIComponent(id)}/unban`
export const userItem = (id: string): string => `${ENDPOINTS.users}/${encodeURIComponent(id)}`
export const roleItem = (name: string): string => `${ENDPOINTS.roles}/${encodeURIComponent(name)}`
export const roleAssign = (name: string): string => `${roleItem(name)}/assign`
export const roleRevoke = (name: string): string => `${roleItem(name)}/revoke`
export const operationItem = (id: string): string => `${ENDPOINTS.operations}/${encodeURIComponent(id)}`
export const inviteRevoke = (token: string): string => `${ENDPOINTS.invites}/${encodeURIComponent(token)}/revoke`
export const notificationRead = (id: string): string => `${ENDPOINTS.notifications}/${encodeURIComponent(id)}/read`
export const inviteAccept = (token: string): string => `${ENDPOINTS.invites}/${encodeURIComponent(token)}/accept`

const enc = encodeURIComponent
export const courseItem = (id: string): string => `${ENDPOINTS.courses}/${enc(id)}`
export const courseProfile = (id: string): string => `${courseItem(id)}/profile`
export const coursePlan = (id: string): string => `${courseItem(id)}/plan`
export const coursePlanValidate = (id: string): string => `${coursePlan(id)}/validate`
export const coursePlanSubmit = (id: string): string => `${coursePlan(id)}/submit`
export const courseGenerate = (id: string): string => `${courseItem(id)}/generate/submit`
export const sourceApprove = (id: string): string => `${ENDPOINTS.sources}/${enc(id)}/approve`
export const runItem = (id: string): string => `${API}/runs/${enc(id)}`
export const courseRuns = (id: string): string => `${courseItem(id)}/runs`
export const runResume = (id: string): string => `${runItem(id)}/resume`

export const learnCourses = `${API}/learn/courses`
export const humanQueue = `${API}/human-queue`
export const humanQueueResolve = (id: string): string => `${humanQueue}/${enc(id)}/resolve`
export const courseSources = (id: string): string => `${courseItem(id)}/sources`
export const courseEstimate = (id: string): string => `${courseItem(id)}/estimate`
export const courseHeld = (id: string): string => `${courseItem(id)}/held`
export const coursePublish = (id: string): string => `${courseItem(id)}/publish`
export const sourceReject = (id: string): string => `${ENDPOINTS.sources}/${enc(id)}/reject`
export const runCancel = (id: string): string => `${runItem(id)}/cancel`
export const blockItem = (id: string): string => `${API}/blocks/${enc(id)}`
export const blockRewrite = (id: string): string => `${blockItem(id)}/rewrite`
export const blockAccept = (id: string): string => `${blockItem(id)}/accept`
export const courseTranslate = (id: string): string => `${courseItem(id)}/translate`
export const courseLearner = (id: string): string => `${courseItem(id)}/learner`
export const courseRewriteHeld = (id: string): string => `${courseItem(id)}/held/rewrite`

export function loginUrl(next?: string): string {
  const q = new URLSearchParams({ provider: 'google' })
  if (next) q.set('next', next)
  return `${API}/auth/login?${q.toString()}`
}

export const LIVE_ENDPOINTS: readonly string[] = [
  ENDPOINTS.csrf, ENDPOINTS.me, ENDPOINTS.logout, ENDPOINTS.users, ENDPOINTS.roles,
  ENDPOINTS.invites, ENDPOINTS.notifications, ENDPOINTS.courses, ENDPOINTS.operationEcho, ENDPOINTS.events, ENDPOINTS.sourceUpload, ENDPOINTS.sourceFetch,
]
