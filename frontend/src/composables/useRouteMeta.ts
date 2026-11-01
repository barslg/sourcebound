import { computed, type ComputedRef } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { docTitle, rowOf, trail, type Crumb, type RouteRow } from '@/router/registry'
import { useDocTitle } from './useDocTitle'

export interface ObjectRef { id: string; title: string }
export interface RouteMeta {
  row: ComputedRef<RouteRow>
  title: ComputedRef<string>
  purpose: ComputedRef<string>
  crumbs: ComputedRef<Crumb[]>
}

export function useRouteMeta(course: () => ObjectRef | null | undefined = () => null): RouteMeta {
  const { t } = useI18n()
  const route = useRoute()
  const row = computed(() => rowOf(String(route.name)))
  const ctx = computed(() => ({ t, courseId: course()?.id ?? null, courseTitle: course()?.title ?? null }))
  const title = computed(() => (row.value.heading === 'object' ? (ctx.value.courseTitle ?? '') : t(row.value.titleKey as string)))
  const purpose = computed(() => t(row.value.purposeKey as string))
  const crumbs = computed(() => (row.value.parent === null ? [] : trail(row.value.name, ctx.value)))
  useDocTitle(() => docTitle(row.value.name, ctx.value))
  return { row, title, purpose, crumbs }
}
