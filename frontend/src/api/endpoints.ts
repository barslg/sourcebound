const API = '/api/v1'

export const ENDPOINTS = {
  health: `${API}/health`,
  csrf: `${API}/auth/csrf`,
  me: `${API}/auth/me`,
  modelKey: `${API}/auth/me/model-key`,
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
export const userRole = (id: string, name: string): string => `${ENDPOINTS.users}/${encodeURIComponent(id)}/roles/${encodeURIComponent(name)}`
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
export const courseBuild = (id: string): string => `${courseItem(id)}/build`
export const courseGenerate = (id: string): string => `${courseItem(id)}/generate/submit`
export const sourceApprove = (id: string): string => `${ENDPOINTS.sources}/${enc(id)}/approve`
export const runItem = (id: string): string => `${API}/runs/${enc(id)}`
export const courseRuns = (id: string): string => `${courseItem(id)}/runs`
export const runCalls = (id: string): string => `${runItem(id)}/calls`
export const runCall = (id: string, callId: string): string => `${runCalls(id)}/${enc(callId)}`
export const runResume = (id: string): string => `${runItem(id)}/resume`

export const learnCourses = `${API}/learn/courses`
export const learnCourse = (id: string): string => `${learnCourses}/${enc(id)}`
export const learnAsk = (id: string): string => `${learnCourse(id)}/ask`
export const learnSources = (id: string): string => `${learnCourse(id)}/sources`
export const learnGlossary = (id: string): string => `${learnCourse(id)}/glossary`
export const learnTopic = (id: string): string => `${API}/learn/topics/${enc(id)}`
export const learnSubmit = (id: string): string => `${learnTopic(id)}/submit`
export const learnPassage = (id: string): string => `${API}/learn/passages/${enc(id)}`
export const learnAnswer = (id: string): string => `${API}/learn/blocks/${enc(id)}/answer`
export const courseReport = (id: string): string => `${courseItem(id)}/report`
export const humanQueue = `${API}/human-queue`
export const humanQueueResolve = (id: string): string => `${humanQueue}/${enc(id)}/resolve`
export const courseSources = (id: string): string => `${courseItem(id)}/sources`
export const courseEstimate = (id: string): string => `${courseItem(id)}/estimate`
export const courseHeld = (id: string): string => `${courseItem(id)}/held`
export const courseVisibility = (id: string): string => `${courseItem(id)}/visibility`
export const coursePublish = (id: string): string => `${courseItem(id)}/publish`
export const sourceReject = (id: string): string => `${ENDPOINTS.sources}/${enc(id)}/reject`
export const runCancel = (id: string): string => `${runItem(id)}/cancel`
export const blockItem = (id: string): string => `${API}/blocks/${enc(id)}`
export const blockRewrite = (id: string): string => `${blockItem(id)}/rewrite`
export const blockAccept = (id: string): string => `${blockItem(id)}/accept`
export const courseTranslate = (id: string): string => `${courseItem(id)}/translate`
export const coursePreview = (id: string): string => `${courseItem(id)}/preview`
export const previewTopic = (topicId: string): string => `${API}/preview/topics/${enc(topicId)}`
export const coursePreviewPassage = (id: string, passageId: string): string => `${coursePreview(id)}/passages/${enc(passageId)}`
export const coursePreviewSources = (id: string): string => `${coursePreview(id)}/sources`
export const coursePreviewGlossary = (id: string): string => `${coursePreview(id)}/glossary`
export const courseRewriteHeld = (id: string): string => `${courseItem(id)}/held/rewrite`
export const courseRecheckHeld = (id: string): string => `${courseItem(id)}/held/recheck`

export function loginUrl(next?: string): string {
  const q = new URLSearchParams({ provider: 'google' })
  if (next) q.set('next', next)
  return `${API}/auth/login?${q.toString()}`
}

export const LIVE_ENDPOINTS: readonly string[] = [
  ENDPOINTS.csrf, ENDPOINTS.me, ENDPOINTS.logout, ENDPOINTS.users, ENDPOINTS.roles,
  ENDPOINTS.invites, ENDPOINTS.notifications, ENDPOINTS.courses, ENDPOINTS.operationEcho, ENDPOINTS.events, ENDPOINTS.sourceUpload, ENDPOINTS.sourceFetch,
]
