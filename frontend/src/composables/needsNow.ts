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
