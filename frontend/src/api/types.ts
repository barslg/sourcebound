export interface Me {
  id: string
  email: string
  display_name: string
  avatar_url: string
  is_superadmin: boolean
  roles: string[]
  permissions: string[]
  scope_tags: string[]
}
export interface UserRow {
  id: string
  email: string
  display_name: string
  is_superadmin: boolean
  is_legacy: boolean
  banned_at: string | null
  banned_reason: string
  created_at: string | null
  roles?: Array<{ name: string; scope_tags: string[] }>
}
export interface Role {
  name: string
  permission_codes: string[]
  crud: Record<string, string[]>
  scope_tags: string[]
}
export interface Invite {
  token: string
  course_tags: string[]
  max_uses: number | null
  used_count: number
  expires_at: string | null
  revoked_at: string | null
}
export interface Operation {
  operation_id: string
  kind: string
  status: string
  progress: number
  result: Record<string, unknown> | null
  error: string | null
}
export interface AcceptResult { scopes: string[] }

export interface Constraint {
  key: string
  kind: string
  value: Record<string, unknown>
  required: boolean
  question: string | null
  answer: Record<string, unknown> | null
}
export interface CourseDoc {
  id: string
  owner_id: string
  title: string
  language: string
  status: string
  scope_tags: string[]
  row_version: number
  published_version_id: string | null
  profile: { methodology_codes: string[]; bloom_level: string; audience: string; dreyfus_start: string } | null
  constraints: Constraint[]
}
export interface CourseRow { id: string; title: string; language: string; status: string; scope_tags: string[] }
export interface Validation { blocking_questions: Array<{ key: string; kind: string; question: string | null }>; can_generate: boolean }
export interface SourceRow { id: string; title: string; media: string; licence: string; as_of: string | null; approval: string; progress: number; ingest_status?: string; ingest_message?: string | null }
export interface HeldBlock { id: string; topic_title: string; brief: string; hold_reason: string; passage: string; address: string }
export interface HeldDoc { blocks: HeldBlock[]; batch_quote_usd: string | null }
export interface Estimate { blocks: number; calls: number; tokens: number; cost_usd: string; seconds: number; note: string }
export interface InviteListItem { id: string; course_tags: string[]; max_uses: number | null; used_count: number; expires_at: string | null; revoked_at: string | null }
export interface NotificationRow { id: string; kind: string; payload: Record<string, unknown>; read: boolean; created_at: string | null }
export interface QueueItem { id: string; kind: string; url: string; reason: string; payload: { course_id?: string }; created_at: string | null }
export interface Permission { code: string; category: string; label: string; description: string }
