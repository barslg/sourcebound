<script setup lang="ts">
import Select from 'primevue/select'
import { computed } from 'vue'
import { CircleCheck, TriangleAlert } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { request } from '@/api/client'
import { coursePreview } from '@/api/endpoints'
import type { LearnOutline, LearnTopicRef } from '@/api/types'
import FormField from '@/components/FormField.vue'
import SyllabusRow from '@/components/learner/SyllabusRow.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import StatusChip from '@/components/StatusChip.vue'
import { COURSE_LANGUAGES } from '@/composables/languages'
import { useReloading } from '@/composables/useReloading'
import { useUrlParam } from '@/composables/useUrlState'

const { t } = useI18n()
const route = useRoute()
const courseId = computed(() => String(route.params.id))
const language = useUrlParam('language', 'en')
const outline = useReloading<LearnOutline>(() => request<LearnOutline>(coursePreview(courseId.value), { query: { lang: language.value } }), () => [courseId.value, language.value])
const topics = computed<LearnTopicRef[]>(() => (outline.data.value?.modules ?? []).flatMap((m) => m.topics))
const heldTotal = computed(() => topics.value.reduce((n, x) => n + (x.held ?? 0), 0))
const topicLink = (topic: LearnTopicRef): string => `/preview/topics/${topic.id}?language=${language.value}`
</script>

<template>
  <PageHeader :course="outline.data.value" />
  <div class="stack">
    <FormField id="p-lang" :label="t('preview.language')" :help="t('preview.languageHint')">
      <Select v-model="language" input-id="p-lang" :options="COURSE_LANGUAGES.map((code) => ({ code, label: t(`language.${code}`) }))" option-label="label" option-value="code" data-testid="preview-language" />
    </FormField>
    <ResourceState :loading="outline.loading.value" :error="outline.error.value" :empty="outline.data.value !== null && topics.length === 0" :empty-text="t('preview.empty')" @retry="outline.reload()">
      <template #empty-actions><router-link :to="`/courses/${courseId}/generate`" data-testid="preview-to-generate">{{ t('preview.toGenerate') }}</router-link></template>
      <div v-if="outline.data.value" class="stack">
        <p class="hint" data-testid="preview-note">{{ heldTotal > 0 ? t('preview.heldNote', heldTotal) : t('preview.readyNote') }}</p>
        <section v-for="m in outline.data.value.modules" :key="m.id" class="syllabus-card" data-testid="module">
          <h3>{{ m.title }}</h3>
          <p v-if="m.purpose" class="hint module-purpose">{{ m.purpose }}</p>
          <ul class="plain-list syllabus">
            <SyllabusRow v-for="x in m.topics" :key="x.id" :to="topicLink(x)" :title="x.title" :module-test="x.kind === 'module_test'" :icon="(x.held ?? 0) > 0 ? TriangleAlert : CircleCheck" :icon-class="(x.held ?? 0) > 0 ? 'tick-held' : 'muted'">
              <template v-if="(x.held ?? 0) > 0" #status><StatusChip tone="warn" :label="t('preview.heldChip', x.held ?? 0)" data-testid="preview-held" /></template>
              <template #end><span class="muted">{{ t('preview.blocks', x.progress.blocks_total) }}</span></template>
            </SyllabusRow>
          </ul>
        </section>
      </div>
    </ResourceState>
  </div>
</template>
