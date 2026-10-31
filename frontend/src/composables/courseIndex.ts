import { onMounted, ref } from 'vue'
import { request } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import type { CourseRow } from '@/api/types'

export interface IndexedCourse { id: string; title: string; tags: string[] }

export function useCourseIndex() {
  const courses = ref<IndexedCourse[]>([])
  const loading = ref(true)
  const error = ref<string | null>(null)
  async function load(): Promise<void> {
    try {
      const rows = await request<CourseRow[]>(ENDPOINTS.courses)
      courses.value = rows.map((r) => ({ id: r.id, title: r.title, tags: r.scope_tags }))
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
