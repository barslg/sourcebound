import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Stage } from '@/composables/needsNow'

export const useCourseContext = defineStore('courseContext', () => {
  const courseId = ref<string | null>(null)
  const title = ref('')
  const stages = ref<Array<Stage & { to: string }>>([])
  function set(id: string, name: string, list: Array<Stage & { to: string }>): void {
    courseId.value = id
    title.value = name
    stages.value = list
  }
  function clear(): void {
    courseId.value = null
    title.value = ''
    stages.value = []
  }
  return { courseId, title, stages, set, clear }
})
