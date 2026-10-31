import { onMounted, ref } from 'vue'
import { request } from '@/api/client'
import { courseItem, ENDPOINTS } from '@/api/endpoints'
import type { CourseDoc, CourseRow } from '@/api/types'

export interface IndexedCourse { id: string; title: string; tags: string[] }

export function useCourseIndex() {
  const courses = ref<IndexedCourse[]>([])
  const loading = ref(true)
  const error = ref<string | null>(null)
  async function load(): Promise<void> {
    try {
      const rows = await request<CourseRow[]>(ENDPOINTS.courses)
      const docs = await Promise.all(rows.map((r) => request<CourseDoc>(courseItem(r.id))))
      courses.value = docs.map((d) => ({ id: d.id, title: d.title, tags: d.scope_tags }))
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : String(cause)
    } finally {
      loading.value = false
    }
  }
  onMounted(() => { void load() })
  return { courses, loading, error, reload: load }
}

export function coursesForTags(courses: IndexedCourse[], tags: string[]): IndexedCourse[] {
  return courses.filter((c) => c.tags.some((tag) => tags.includes(tag)))
}
