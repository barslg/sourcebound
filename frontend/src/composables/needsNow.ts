export interface CourseFacts {
  status: string
  profile: boolean
  plan: boolean
  openQuestions: number
  published: boolean
}

export interface Action { key: string; to: string | null; params?: Record<string, unknown> }

export interface StageFacts extends CourseFacts {
  courseId: string
  firstQuestionKey: string | null
  sourceCount: number
  pendingSources: number
  firstPendingId: string | null
  held: number
  firstHeldId: string | null
  draft: number
  generated: boolean
  live?: boolean
  planStopped?: string | null
}

export interface CourseCounters {
  id: string
  status: string
  has_profile?: boolean
  has_plan?: boolean
  open_questions?: number
  held_count: number
  draft_count: number
  live_work?: number
  pending_sources: number
  source_count: number
}

export function factsOfRow(c: CourseCounters): StageFacts {
  return {
    courseId: c.id,
    status: c.status,
    profile: c.has_profile ?? false,
    plan: c.has_plan ?? false,
    openQuestions: c.open_questions ?? 0,
    published: c.status === 'published',
    firstQuestionKey: null,
    sourceCount: c.source_count,
    pendingSources: c.pending_sources,
    firstPendingId: null,
    held: c.held_count,
    firstHeldId: null,
    draft: c.live_work ? 0 : c.draft_count,
    live: (c.live_work ?? 0) > 0,
    generated: c.status === 'generated' || c.status === 'published',
  }
}

export function nextAction(c: StageFacts): Action {
  const base = `/courses/${c.courseId}`
  if (c.published) return { key: 'needs.nothing', to: null }
  if (c.live) return { key: 'needs.running', to: `${base}/runs#runs-list` }
  if (c.planStopped && !c.plan) return { key: 'needs.planStopped', to: `${base}/plan#build-plan`, params: { reason: c.planStopped } }
  if (c.pendingSources > 0 && !c.generated) return { key: 'needs.sources', to: `${base}/sources#${c.firstPendingId ? `source-${c.firstPendingId}` : 'sources-list'}` }
  if (!c.profile || !c.plan) return { key: c.profile ? 'needs.plan' : 'needs.profile', to: `${base}/plan#build-plan` }
  if (c.openQuestions > 0) return { key: 'needs.questions', to: `${base}/plan#${c.firstQuestionKey ? `question-${c.firstQuestionKey}` : 'questions'}` }
  if (!c.generated) return { key: 'needs.generate', to: `${base}/generate#generate` }
  if (c.held > 0) return { key: 'needs.fix', to: `${base}/fix#${c.firstHeldId ? `held-${c.firstHeldId}` : 'held-list'}` }
  if (c.draft > 0) return { key: 'needs.unread', to: `${base}/fix#held-list` }
  return { key: 'needs.publish', to: `${base}/publish#publish-open` }
}

export type StageState = 'done' | 'needs' | 'blocked' | 'later'
export interface Stage { id: 'sources' | 'plan' | 'generate' | 'fix' | 'publish'; state: StageState; why: string | null }

export function stages(c: StageFacts, sourceCount: number): Stage[] {
  const planReady = c.profile && c.plan
  return [
    { id: 'sources', state: sourceCount === 0 ? 'later' : c.pendingSources > 0 ? 'needs' : 'done', why: null },
    { id: 'plan', state: !planReady ? 'needs' : c.openQuestions > 0 ? 'needs' : 'done', why: null },
    { id: 'generate', state: c.generated ? 'done' : !planReady ? 'blocked' : c.openQuestions > 0 ? 'blocked' : 'needs', why: !planReady ? 'plan' : c.openQuestions > 0 ? 'questions' : null },
    { id: 'fix', state: !c.generated ? 'later' : c.held > 0 || c.draft > 0 ? 'needs' : 'done', why: null },
    { id: 'publish', state: c.published ? 'done' : !c.generated ? 'later' : c.held > 0 || c.draft > 0 ? 'blocked' : 'needs', why: c.held > 0 ? 'held' : c.draft > 0 ? 'unread' : null },
  ]
}
