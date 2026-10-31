<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { ApiError, request } from '@/api/client'
import { courseItem, courseLearner } from '@/api/endpoints'
import type { CourseDoc, LearnerBlock, LearnerDoc } from '@/api/types'
import FormField from '@/components/FormField.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import { COURSE_LANGUAGES } from '@/composables/languages'
import { useResource } from '@/composables/useResource'
import { useUrlParam } from '@/composables/useUrlState'

const { t } = useI18n()
const route = useRoute()
const courseId = computed(() => String(route.params.id))
const course = useResource<CourseDoc>(`course:${courseId.value}`, (signal) => request<CourseDoc>(courseItem(courseId.value), { signal }))
const language = useUrlParam('language', 'en')
const doc = ref<LearnerDoc | null>(null)
const error = ref<ApiError | null>(null)
const loading = ref(true)
let ticket = 0

async function load(): Promise<void> {
  const mine = ++ticket
  loading.value = doc.value === null
  try {
    const next = await request<LearnerDoc>(courseLearner(courseId.value), { query: { lang: language.value } })
    if (mine !== ticket) return
    doc.value = next
    error.value = null
  } catch (cause) {
    if (mine === ticket) error.value = cause instanceof ApiError ? cause : new ApiError(0, String(cause))
  } finally {
    if (mine === ticket) loading.value = false
  }
}
watch(language, () => { void load() }, { immediate: true })

const crumbs = computed(() => [
  { label: t('nav.courses'), to: '/courses' },
  ...(course.data.value ? [{ label: course.data.value.title, to: `/courses/${courseId.value}/publish` }] : []),
  { label: t('preview.title') },
])
const TEXT_KEYS = ['text', 'body', 'stem', 'prompt', 'question', 'title']
function textOf(block: LearnerBlock): string {
  for (const key of TEXT_KEYS) {
    const value = block.content[key]
    if (typeof value === 'string' && value.trim() !== '') return value
  }
  return ''
}
</script>

<template>
  <PageHeader :title="t('preview.title')" :help="t('preview.help')" :crumbs="crumbs" :doc-title="`${t('preview.title')} · ${course.data.value?.title ?? ''} · Sourcebound`" />
  <div class="stack">
    <FormField id="p-lang" :label="t('preview.language')" :help="t('preview.languageHint')">
      <select id="p-lang" v-model="language" class="native-select" data-testid="preview-language">
        <option v-for="code in COURSE_LANGUAGES" :key="code" :value="code">{{ t(`language.${code}`) }}</option>
      </select>
    </FormField>
    <ResourceState :loading="loading" :error="error" :empty="doc !== null && doc.blocks.length === 0" :empty-text="t('preview.empty')" @retry="load">
      <ol v-if="doc" class="stack plain-list" data-testid="preview-blocks">
        <li v-for="(block, index) in doc.blocks" :key="block.plan_node_id" class="bell-item stack" data-testid="preview-block" :lang="block.language">
          <strong>{{ t('preview.block', { n: index + 1 }) }}</strong>
          <p v-if="textOf(block)">{{ textOf(block) }}</p>
          <p v-else class="muted">{{ t('preview.noText') }}</p>
          <p v-if="block.fallback_notice" class="preview-notice" role="note" data-testid="preview-notice">{{ block.fallback_notice }}</p>
        </li>
      </ol>
    </ResourceState>
  </div>
</template>
