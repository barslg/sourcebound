import { describe, expect, it } from 'vitest'
import { humanize, pluralUk, reasonKey, usd } from '../../src/composables/labels'
import { nextAction, stages, type StageFacts } from '../../src/composables/needsNow'

const base: StageFacts = {
  sourceCount: 1,
  draft: 0,
  courseId: 'c1', status: 'draft', profile: true, plan: true, openQuestions: 0, published: false,
  firstQuestionKey: null, pendingSources: 0, firstPendingId: null, held: 0, firstHeldId: null, generated: false,
}

describe('labels', () => {
  it('turns identifiers into words and never leaves an underscore', () => {
    expect(humanize('audience_level')).toBe('Audience level')
    expect(humanize('scenario_practice')).toBe('Scenario practice')
    expect(humanize('section.courses')).toBe('Section courses')
  })
  it('prints money with a currency sign and two decimals', () => {
    expect(usd('0.124500')).toBe('$0.12')
    expect(usd('0')).toBe('$0.00')
    expect(usd(null)).toBeNull()
    expect(usd('x')).toBeNull()
  })
  it('keys reasons by permission code', () => {
    expect(reasonKey('course.create')).toBe('reason.course_create')
  })
  it('picks the Ukrainian plural form: many, one, few', () => {
    expect([1, 2, 5, 11, 21, 22, 25, 0].map(pluralUk)).toEqual([1, 2, 0, 0, 1, 2, 0, 0])
  })
})

describe('next action names the control that resolves it', () => {
  it('walks the stages in order and links an anchor on the target page', () => {
    expect(nextAction({ ...base, profile: false, plan: false }).to).toBe('/courses/c1/plan#build-plan')
    expect(nextAction({ ...base, openQuestions: 1, firstQuestionKey: 'audience_level' })).toEqual({ key: 'needs.questions', to: '/courses/c1/plan#question-audience_level' })
    expect(nextAction({ ...base, pendingSources: 2, firstPendingId: 's9' }).to).toBe('/courses/c1/sources#source-s9')
    expect(nextAction({ ...base, generated: true, held: 1, firstHeldId: 'b1' }).to).toBe('/courses/c1/fix#held-b1')
    expect(nextAction({ ...base, generated: true }).to).toBe('/courses/c1/publish#publish-open')
    expect(nextAction(base).to).toBe('/courses/c1/generate#generate')
    expect(nextAction({ ...base, published: true }).to).toBeNull()
  })
  it('gives each stage one state', () => {
    const s = Object.fromEntries(stages({ ...base, generated: true, held: 2 }, 3).map((x) => [x.id, x.state]))
    expect(s).toEqual({ sources: 'done', plan: 'done', generate: 'done', fix: 'needs', publish: 'blocked' })
    const early = Object.fromEntries(stages({ ...base, profile: false, plan: false }, 0).map((x) => [x.id, x.state]))
    expect(early).toEqual({ sources: 'needs', plan: 'needs', generate: 'blocked', fix: 'later', publish: 'later' })
  })
})
