export interface CourseFacts {
  status: string
  profile: boolean
  plan: boolean
  openQuestions: number
  published: boolean
}

export function needsNow(c: CourseFacts): string {
  if (c.published) return 'needs.nothing'
  if (!c.profile) return 'needs.profile'
  if (!c.plan) return 'needs.plan'
  if (c.openQuestions > 0) return 'needs.questions'
  if (c.status === 'generated') return 'needs.publish'
  return 'needs.generate'
}

export interface Action { key: string; to: string | null }

export interface StageFacts extends CourseFacts {
  courseId: string
  firstQuestionKey: string | null
  pendingSources: number
  firstPendingId: string | null
  held: number
  firstHeldId: string | null
  generated: boolean
}

export function nextAction(c: StageFacts): Action {
  const base = `/courses/${c.courseId}`
  if (c.published) return { key: 'needs.nothing', to: null }
  if (!c.profile || !c.plan) return { key: c.profile ? 'needs.plan' : 'needs.profile', to: `${base}/plan#build-plan` }
  if (c.openQuestions > 0) return { key: 'needs.questions', to: `${base}/plan#${c.firstQuestionKey ? `question-${c.firstQuestionKey}` : 'questions'}` }
  if (c.pendingSources > 0 && !c.generated) return { key: 'needs.sources', to: `${base}/sources#${c.firstPendingId ? `source-${c.firstPendingId}` : 'sources-list'}` }
  if (c.held > 0) return { key: 'needs.fix', to: `${base}/fix#${c.firstHeldId ? `held-${c.firstHeldId}` : 'held-list'}` }
  if (c.generated) return { key: 'needs.publish', to: `${base}/publish#publish-open` }
  return { key: 'needs.generate', to: `${base}/plan#generate` }
}

export type StageState = 'done' | 'needs' | 'blocked' | 'later'
export interface Stage { id: 'sources' | 'plan' | 'generate' | 'fix' | 'publish'; state: StageState; why: string | null }

export function stages(c: StageFacts, sourceCount: number): Stage[] {
  const planReady = c.profile && c.plan
  return [
    { id: 'sources', state: sourceCount === 0 ? 'needs' : c.pendingSources > 0 ? 'needs' : 'done', why: null },
    { id: 'plan', state: !planReady ? 'needs' : c.openQuestions > 0 ? 'needs' : 'done', why: null },
    { id: 'generate', state: c.generated ? 'done' : !planReady ? 'blocked' : c.openQuestions > 0 ? 'blocked' : 'needs', why: !planReady ? 'plan' : c.openQuestions > 0 ? 'questions' : null },
    { id: 'fix', state: !c.generated ? 'later' : c.held > 0 ? 'needs' : 'done', why: null },
    { id: 'publish', state: c.published ? 'done' : !c.generated ? 'later' : c.held > 0 ? 'blocked' : 'needs', why: c.held > 0 ? 'held' : null },
  ]
}
