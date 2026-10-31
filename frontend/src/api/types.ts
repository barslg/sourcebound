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
  id: string
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
export interface RunListItem { id: string; status: string; kind: string | null; created_at: string; stop_marker: string | null; cost_so_far: string }

export interface Constraint {
  key: string
  kind: string
  value: Record<string, unknown>
  required: boolean
  question: string | null
  answer: Record<string, unknown> | null
}
export interface TranslationRow { language: string; status: 'ready' | 'partial' | 'failed'; ok: number; held: number; failed: number }
export interface LearnerBlock { plan_node_id: string; block_type: string; content: Record<string, unknown>; language: string; fallback_notice: string | null }
export interface LearnerDoc { course_id: string; language: string; source_language: string; available_languages: Array<{ language: string; status: string }>; blocks: LearnerBlock[] }
export interface CourseDoc {
  id: string
  owner_id: string
  title: string
  language: string
  status: string
  scope_tags: string[]
  row_version: number
  published_version_id: string | null
  current_version_id: string | null
  topic_count: number
  block_count: number
  translations: TranslationRow[]
  held_count: number
  draft_count: number
  pending_sources: number
  source_count: number
  profile: { methodology_codes: string[]; bloom_level: string; audience: string; dreyfus_start: string } | null
  constraints: Constraint[]
}
export interface CourseRow { id: string; title: string; language: string; status: string; scope_tags: string[]; has_profile: boolean; has_plan: boolean; open_questions: number; held_count: number; draft_count: number; pending_sources: number; source_count: number }
export interface Validation { blocking_questions: Array<{ key: string; kind: string; question: string | null }>; can_generate: boolean }
export interface SourceRow { id: string; title: string; media: string; licence: string; as_of: string | null; approval: string; progress: number; ingest_status?: string; ingest_message?: string | null }
export interface HeldBlock { id: string; topic_title: string; brief: string; hold_reason: string; passage: string; address: string }
export interface HeldDoc { blocks: HeldBlock[]; batch_quote_usd: string | null }
export interface Estimate { blocks: number; calls: number; tokens: number; cost_usd: string; seconds: number; note: string }
export interface InviteListItem { id: string; course_tags: string[]; max_uses: number | null; used_count: number; expires_at: string | null; revoked_at: string | null }
export interface NotificationRow { id: string; kind: string; payload: Record<string, unknown>; read: boolean; created_at: string | null }
export interface QueueItem { id: string; kind: string; url: string; reason: string; payload: { course_id?: string }; created_at: string | null }
export interface Permission { code: string; category: string; label: string; description: string }
export interface LearnCourse { id: string; title: string; language: string; audience: string; progress: { blocks_total: number; blocks_attempted: number; completion: number; status: string } }
