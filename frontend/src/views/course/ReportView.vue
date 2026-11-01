<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { request } from '@/api/client'
import { courseItem, courseReport } from '@/api/endpoints'
import type { CourseDoc, CourseReport, KindCounts } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import NoValue from '@/components/NoValue.vue'
import PageHeader from '@/components/PageHeader.vue'
import RelTime from '@/components/RelTime.vue'
import ResourceState from '@/components/ResourceState.vue'
import Section from '@/components/Section.vue'
import StatusChip from '@/components/StatusChip.vue'
import { useResource } from '@/composables/useResource'

const { t } = useI18n()
const route = useRoute()
const id = computed(() => String(route.params.id))
const course = useResource<CourseDoc>(`course:${id.value}`, (signal) => request<CourseDoc>(courseItem(id.value), { signal }))
const report = useResource<CourseReport>(`report:${id.value}`, (signal) => request<CourseReport>(courseReport(id.value), { signal }))
const csv = computed(() => `${courseReport(id.value)}?format=csv`)
const tone = (status: string): 'ok' | 'info' => (status === 'mastered' || status === 'complete' ? 'ok' : 'info')
const kinds = (rec: Record<string, KindCounts>): Array<[string, KindCounts]> => Object.entries(rec)
const percent = (x: number): string => `${Math.round(x * 100)}%`
function download(): void { window.location.assign(csv.value) }
</script>

<template>
  <PageHeader :course="course.data.value">
    <template #actions><ActionButton action="export" :label="t('report.export')" data-testid="report-export" @click="download" /></template>
  </PageHeader>
  <ResourceState :loading="report.loading.value" :error="report.error.value" :empty="report.data.value !== null && report.data.value.learners.length === 0" :empty-text="report.data.value?.published === false ? t('report.notPublished') : t('report.empty')" @retry="report.reload()">
    <template #empty-actions><router-link :to="`/invites?course=${id}`" data-testid="report-invite">{{ t('report.inviteLink') }}</router-link></template>
    <div v-if="report.data.value" class="stack">
      <section v-for="l in report.data.value.learners" :key="l.user_id" class="bell-item stack" data-testid="report-learner">
        <p class="row"><strong>{{ l.display_name || l.email }}</strong><span class="muted">{{ l.email }}</span><span class="muted">{{ t('report.lastActivity') }} <RelTime v-if="l.last_activity" :value="l.last_activity" /><NoValue v-else /></span></p>
        <table class="app-table-grid" :aria-label="t('report.topicsCaption', { name: l.display_name || l.email })">
          <thead><tr><th scope="col">{{ t('report.topic') }}</th><th scope="col">{{ t('report.completion') }}</th><th scope="col">{{ t('report.mastery') }}</th><th scope="col">{{ t('report.status') }}</th></tr></thead>
          <tbody>
            <tr v-for="x in l.topics" :key="x.topic_id" data-testid="report-topic">
              <th scope="row">{{ x.title }}</th>
              <td>{{ percent(x.completion) }}</td>
              <td><template v-if="x.mastery !== null">{{ percent(x.mastery) }}</template><NoValue v-else /></td>
              <td><StatusChip :tone="tone(x.status)" :label="t(`learn.status.${x.status}`)" /></td>
            </tr>
          </tbody>
        </table>
        <template v-if="kinds(l.find_the_error).length > 0">
          <p class="hint">{{ t('report.errorsCaption') }}</p>
          <ul class="plain-list" data-testid="report-kinds"><li v-for="[kind, c] in kinds(l.find_the_error)" :key="kind">{{ t(`learn.kind.${kind}`) }}: {{ t('learn.counters.cells', { hits: c.hits, alarms: c.false_alarms, misses: c.misses }) }}</li></ul>
        </template>
      </section>
      <Section id="course-errors" :title="t('report.courseErrors')" :help="t('report.courseErrorsHelp')" />
      <p v-if="kinds(report.data.value.find_the_error).length === 0" class="muted">{{ t('report.noErrors') }}</p>
      <ul v-else class="plain-list" data-testid="report-course-kinds"><li v-for="[kind, c] in kinds(report.data.value.find_the_error)" :key="kind">{{ t(`learn.kind.${kind}`) }}: {{ t('learn.counters.cells', { hits: c.hits, alarms: c.false_alarms, misses: c.misses }) }}</li></ul>
    </div>
  </ResourceState>
</template>
