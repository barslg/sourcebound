export interface Me {
  id: string
  email: string
  display_name: string
  avatar_url: string
  is_superadmin: boolean
  roles: string[]
  permissions: string[]
  scope_tags: string[]
  /** True when a run started by this account has a Meta key to bill: their own, or the local stand's. */
  model_key_ready?: boolean
}
export interface ModelKeyStatus { is_set: boolean; hint: string; updated_at: string | null }
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

export interface CallTotals { tokens_input: number; tokens_cached: number; tokens_output: number; tokens_reasoning: number; cost_usd: string }
export interface CallSummaryRow extends CallTotals { agent: string; model: string; display_name: string; calls: number }
export interface CallRow extends CallTotals {
  id: string
  created_at: string
  agent: string
  step_key: string | null
  model: string
  display_name: string
  endpoint: string
  response_id: string | null
  status: string
  cached_reply: boolean
  duration_ms: number
}
export interface RunNumbers { blocks_accepted: number | null; blocks_held: number | null; quotes_verbatim: number | null; quotes_total: number | null }
export interface RunCallsDoc { summary: CallSummaryRow[]; calls: CallRow[]; total: number; next_offset: number | null; numbers: RunNumbers }
export interface RunCallDetail { id: string; agent: string; model: string; display_name: string; status: string; request: Record<string, unknown>; response: unknown }

export interface Constraint {
  key: string
  kind: string
  value: Record<string, unknown>
  required: boolean
  question: string | null
  answer: Record<string, unknown> | null
}
export interface TranslationRow { language: string; status: 'ready' | 'partial' | 'failed'; ok: number; held: number; failed: number }
export interface CourseDoc {
  id: string
  owner_id: string
  title: string
  language: string
  status: string
  scope_tags: string[]
  row_version: number
  published_version_id: string | null
  is_public: boolean
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
export interface Validation { blocking_questions: Array<{ key: string; kind: string; question: string | null }>; tree_errors: string[]; can_generate: boolean }
export interface SourceRow { id: string; title: string; media: string; licence: string; as_of: string | null; approval: string; progress: number; ingest_status?: string; ingest_message?: string | null }
export interface HeldBlock { id: string; topic_title: string; brief: string; hold_reason: string; passage: string; address: string }
export interface HeldDoc { blocks: HeldBlock[]; batch_quote_usd: string | null }
export interface Estimate { blocks: number; calls: number; tokens: number; cost_usd: string; seconds: number; note: string }
export interface InviteListItem { id: string; course_tags: string[]; max_uses: number | null; used_count: number; expires_at: string | null; revoked_at: string | null }
export interface NotificationRow { id: string; kind: string; payload: Record<string, unknown>; read: boolean; created_at: string | null; operation_kind: string; status: string; error: string | null; run_id: string | null; course_title: string | null }
export interface QueueItem { id: string; kind: string; url: string; reason: string; payload: { course_id?: string }; created_at: string | null }
export interface Permission { code: string; category: string; label: string; description: string }
export interface LearnProgress { blocks_total: number; blocks_attempted: number; completion: number; assessed: number; mastery: number | null; status: string }
export interface LearnCourse { id: string; title: string; language: string; audience: string; is_public: boolean; progress: LearnProgress }
export interface LearnTopicRef { id: string; kind: string; title: string; objectives: string[]; progress: LearnProgress; held?: number }
export interface LearnModule { id: string; title: string; purpose: string; topics: LearnTopicRef[] }
export interface LearnOutline { id: string; title: string; language: string; source_language: string; available_languages: Array<{ language: string; status: string }>; progress: LearnProgress; modules: LearnModule[]; source_count?: number; term_count?: number }
export interface Chip { handle: string; source_number: number; source_title: string; address_label: string; address_short?: string; passage_id: string }
export interface Term { term: string; definition: string }
export interface SourceEntry { number: number; id: string; title: string; kind: string; media: string; licence: string; as_of: string | null; address: string; passages_cited: number; blocks_cited: number }
export interface SourcesPage { course_id: string; title: string; sources: SourceEntry[] }
export interface GlossaryEntry { term: string; definition: string; topic_id: string; topic_title: string; passage_id: string | null; block_id: string | null }
export interface GlossaryPage { course_id: string; title: string; terms: GlossaryEntry[] }
export interface Attempts { count: number; best_score: number | null; last_score: number | null }
export interface LearnBlockView { id: string; block_type: string; role: string; ordinal: number; content: Record<string, unknown>; citations: Chip[]; language: string; fallback_notice: string | null; attempts: Attempts; passage_options?: Chip[]; status?: string; hold_reason?: string }
export interface LearnTopic { id: string; course_id: string; course_title?: string; module_id: string; kind: string; title: string; objectives: string[]; language: string; progress: LearnProgress; blocks: LearnBlockView[]; next_note?: string; terms?: Term[] }
export interface LearnPassage { id: string; text: string; address_label: string; source_title: string; source_number?: number; as_of: string | null; course_id: string }
export interface KindCounts { hits: number; false_alarms: number; misses: number }
export interface SpotFeedback { wrong_sentence: string; true_statement: string; passage: Chip | null; habit_line: string }
export interface AnswerResult {
  block_id: string
  correct: boolean
  partial: boolean
  score: number
  feedback: { habit: string } | SpotFeedback
  hint_from_source: Chip | null
  attempt_no: number
  source_links: Chip[]
  counters?: Record<string, unknown>
  topic_mastery?: LearnProgress
}
export interface SubmitResult { topic_id: string; score: number; unanswered: number; results: AnswerResult[]; mastery: LearnProgress }
export type AskReply = { mode: 'quotes'; quotes: Array<{ quote: string; chip: Chip }> } | { mode: 'not_in_materials'; quotes: []; hint: string }
export interface ReportTopic { topic_id: string; title: string; kind: string; completion: number; mastery: number | null; status: string; last_activity: string | null }
export interface ReportLearner { user_id: string; display_name: string; email: string; last_activity: string | null; topics: ReportTopic[]; find_the_error: Record<string, KindCounts> }
export interface CourseReport { course_id: string; title: string; published: boolean; learners: ReportLearner[]; find_the_error: Record<string, KindCounts> }
