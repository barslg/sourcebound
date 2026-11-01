import type { ActionId } from '../ui/actionMeta'
import type { SectionEntry } from './guard'

export type PageState = 'loading' | 'empty' | 'error' | 'denied' | 'queued'
export type ObjectKind = 'course' | 'learn' | 'user' | 'role' | 'invite' | 'run' | 'none'
export type NavIconId = 'courses' | 'users' | 'invites' | 'roles' | 'learn'
export type Access = { kind: 'public' } | { kind: 'signedIn' } | { kind: 'perm'; code: string }

export interface NavEntry { section: 'main' | 'course'; icon: NavIconId | null; labelKey: string | null }

export interface RouteRow {
  name: string
  path: string
  redirect?: string
  titleKey: string | null
  purposeKey: string | null
  parent: string | null
  nav: NavEntry | null
  access: Access
  object: ObjectKind
  heading: 'page' | 'object'
  states: readonly PageState[]
  primaryAction: ActionId | null
  bare: boolean
  entry: 'ui' | 'external' | 'operation'
}

const LIST: readonly PageState[] = ['loading', 'empty', 'error', 'denied']
const ONE: readonly PageState[] = ['loading', 'error', 'denied']

const row = <const N extends string>(r: Partial<RouteRow> & { name: N; path: string; access: Access }): RouteRow & { name: N } => ({
  redirect: undefined,
  titleKey: null,
  purposeKey: null,
  parent: null,
  nav: null,
  object: 'none',
  heading: 'page',
  states: [],
  primaryAction: null,
  bare: false,
  entry: 'ui',
  ...r,
})

const stage = <const I extends 'sources' | 'plan' | 'generate' | 'fix' | 'publish'>(id: I, states: readonly PageState[], primaryAction: ActionId | null, access: Access) =>
  row({
    name: `course-${id}` as `course-${I}`,
    path: `/courses/:id/${id}`,
    titleKey: `course.stage.${id}`,
    purposeKey: `course.help.${id}`,
    parent: 'courses',
    nav: { section: 'course', icon: null, labelKey: null },
    access,
    object: 'course',
    heading: 'object',
    states,
    primaryAction,
  })

const courses: Access = { kind: 'perm', code: 'course.edit' }

export const REGISTRY = [
  row({ name: 'signin', path: '/signin', titleKey: 'signin.title', purposeKey: 'app.tagline', access: { kind: 'public' }, bare: true, primaryAction: 'signin' }),
  row({ name: 'invite', path: '/invite/:token', titleKey: 'invite.title', purposeKey: 'invite.help', access: { kind: 'public' }, bare: true, object: 'invite', primaryAction: 'signin', entry: 'external' }),
  row({ name: 'waiting', path: '/waiting', titleKey: 'waiting.title', purposeKey: 'waiting.body', access: { kind: 'signedIn' }, bare: true, primaryAction: 'check-again' }),
  row({ name: 'home', path: '/', redirect: '/courses', access: { kind: 'signedIn' } }),
  row({ name: 'courses', path: '/courses', titleKey: 'courses.title', purposeKey: 'courses.help', access: courses, nav: { section: 'main', icon: 'courses', labelKey: 'nav.courses' }, states: LIST, primaryAction: 'create' }),
  stage('sources', LIST, 'create', courses),
  stage('plan', [...LIST, 'queued'], 'add-module', courses),
  stage('generate', [...LIST, 'queued'], 'generate', courses),
  stage('fix', [...LIST, 'queued'], 'rewrite', courses),
  row({ name: 'course-preview', path: '/courses/:id/preview', titleKey: 'preview.title', purposeKey: 'preview.help', parent: 'course-publish', access: { kind: 'perm', code: 'learner.view' }, object: 'course', states: ONE }),
  stage('publish', [...ONE, 'queued'], 'publish', courses),
  row({ name: 'course-runs', path: '/courses/:id/runs', titleKey: 'runs.title', purposeKey: 'runs.help', parent: 'course-plan', access: { kind: 'perm', code: 'run.start' }, object: 'course', heading: 'page', states: LIST }),
  row({ name: 'run', path: '/runs/:runId', titleKey: 'run.title', purposeKey: 'run.help', parent: 'course-plan', access: { kind: 'perm', code: 'run.start' }, object: 'run', states: ONE, primaryAction: 'resume', entry: 'operation' }),
  row({ name: 'users', path: '/users', titleKey: 'users.title', purposeKey: 'users.help', access: { kind: 'perm', code: 'user.list' }, nav: { section: 'main', icon: 'users', labelKey: 'nav.users' }, states: LIST }),
  row({ name: 'invites', path: '/invites', titleKey: 'invites.title', purposeKey: 'invites.intro', access: { kind: 'perm', code: 'invite.create' }, nav: { section: 'main', icon: 'invites', labelKey: 'nav.invites' }, states: LIST, primaryAction: 'create' }),
  row({ name: 'roles', path: '/roles', titleKey: 'roles.title', purposeKey: 'roles.help', access: { kind: 'perm', code: 'role.edit' }, nav: { section: 'main', icon: 'roles', labelKey: 'nav.roles' }, states: LIST, primaryAction: 'create' }),
  row({ name: 'learn', path: '/learn', titleKey: 'learn.title', purposeKey: 'learn.help', access: { kind: 'perm', code: 'learn.read' }, nav: { section: 'main', icon: 'learn', labelKey: 'nav.learn' }, states: LIST }),
  row({ name: 'learn-course', path: '/learn/courses/:id', titleKey: 'learnCourse.title', purposeKey: 'learnCourse.help', parent: 'learn', access: { kind: 'perm', code: 'learn.read' }, object: 'learn', heading: 'object', states: LIST, primaryAction: 'continue' }),
  row({ name: 'learn-topic', path: '/learn/topics/:id', titleKey: 'learnTopic.title', purposeKey: 'learnTopic.help', parent: 'learn-course', access: { kind: 'perm', code: 'learn.read' }, object: 'learn', states: LIST }),
  row({ name: 'course-report', path: '/courses/:id/report', titleKey: 'report.title', purposeKey: 'report.help', parent: 'course-publish', access: courses, object: 'course', heading: 'object', states: LIST, primaryAction: 'export' }),
  row({ name: 'not-found', path: '/:rest(.*)*', titleKey: 'notFound.title', purposeKey: 'notFound.help', access: { kind: 'signedIn' }, bare: false, entry: 'external' }),
] as const satisfies readonly RouteRow[]

export type RouteName = (typeof REGISTRY)[number]['name']
export const ROWS: readonly RouteRow[] = REGISTRY

export const CATCH_ALL = 'not-found'
export const STAGE_TO_ROUTE: Readonly<Record<string, RouteName>> = {
  sources: 'course-sources',
  plan: 'course-plan',
  generate: 'course-generate',
  fix: 'course-fix',
  publish: 'course-publish',
}
export const EXTERNAL_ORIGINS: readonly string[] = []
export const BACKEND_HREF_PREFIXES: readonly string[] = ['/api/v1/auth/']

export function rowOf(name: string): RouteRow {
  const found = ROWS.find((r) => r.name === name)
  if (!found) throw new Error(`route ${name} is not in the registry`)
  return found
}

export function pathFor(name: string, params: Record<string, string> = {}): string {
  return rowOf(name).path.replace(/:(\w+)/g, (_, key: string) => encodeURIComponent(params[key] ?? ''))
}

export function matchRow(path: string): RouteRow | null {
  const segments = path.split('?')[0]?.split('#')[0]?.split('/').filter(Boolean) ?? []
  return (
    ROWS.find((r) => {
      if (r.name === CATCH_ALL) return false
      const want = r.path.split('/').filter(Boolean)
      return want.length === segments.length && want.every((w, i) => w.startsWith(':') || w === segments[i])
    }) ?? null
  )
}

export function paramsOf(row: RouteRow, path: string): Record<string, string> {
  const want = row.path.split('/').filter(Boolean)
  const have = path.split('?')[0]?.split('#')[0]?.split('/').filter(Boolean) ?? []
  const out: Record<string, string> = {}
  want.forEach((w, i) => { if (w.startsWith(':')) out[w.slice(1)] = decodeURIComponent(have[i] ?? '') })
  return out
}

export function allows(row: RouteRow, who: { signedIn: boolean; isSuperadmin: boolean; permissions: readonly string[] }): boolean {
  if (row.access.kind === 'public') return true
  if (!who.signedIn) return false
  if (row.access.kind === 'signedIn') return true
  return who.isSuperadmin || who.permissions.includes(row.access.code)
}

export interface Crumb { label: string; to?: string }
export interface TrailContext {
  t: (key: string) => string
  courseId: string | null
  courseTitle: string | null
}

export function trail(name: string, ctx: TrailContext): Crumb[] {
  const current = rowOf(name)
  const chain: RouteRow[] = []
  for (let r = current.parent ? rowOf(current.parent) : null; r; r = r.parent ? rowOf(r.parent) : null) chain.unshift(r)
  const out: Crumb[] = []
  for (const r of chain) {
    if (r.object === 'course' || r.object === 'learn') {
      if (ctx.courseTitle && ctx.courseId) out.push({ label: ctx.courseTitle, to: pathFor(r.name, { id: ctx.courseId }) })
    } else out.push({ label: ctx.t(r.titleKey as string), to: pathFor(r.name) })
  }
  out.push({ label: current.heading === 'object' ? (ctx.courseTitle ?? '') : ctx.t(current.titleKey as string) })
  return out
}

export function docTitle(name: string, ctx: TrailContext): string {
  const r = rowOf(name)
  return [ctx.t(r.titleKey as string), r.object === 'course' || r.object === 'learn' ? ctx.courseTitle : null, 'Sourcebound'].filter((x): x is string => Boolean(x)).join(' · ')
}

export function navEntries(section: 'main' | 'course'): RouteRow[] {
  return ROWS.filter((r) => r.nav?.section === section)
}

export function sectionEntries(): SectionEntry[] {
  return navEntries('main').flatMap((r) => (r.access.kind === 'perm' ? [{ path: r.path, perm: r.access.code }] : []))
}
