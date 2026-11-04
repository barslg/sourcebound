<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { request } from '@/api/client'
import { courseItem, courseRuns } from '@/api/endpoints'
import type { CourseDoc, RunListItem } from '@/api/types'
import Column from 'primevue/column'
import AppTable from '@/components/AppTable.vue'
import PageHeader from '@/components/PageHeader.vue'
import RelTime from '@/components/RelTime.vue'
import ResourceState from '@/components/ResourceState.vue'
import StatusChip from '@/components/StatusChip.vue'
import { usd } from '@/composables/labels'
import { useResource } from '@/composables/useResource'

const { t, te } = useI18n()
const route = useRoute()
const id = computed(() => String(route.params.id))
const course = useResource<CourseDoc>(`course:${id.value}`, (signal) => request<CourseDoc>(courseItem(id.value), { signal }))
const runs = useResource<RunListItem[]>(`runs:${id.value}`, (signal) => request<RunListItem[]>(courseRuns(id.value), { signal }))
const kindName = (kind: string | null): string => (kind && te(`runs.kind.${kind}`) ? t(`runs.kind.${kind}`) : t('runs.kind.generate'))
</script>

<template>
  <PageHeader :course="course.data.value" />
  <div id="runs-list">
  <ResourceState :loading="runs.loading.value" :error="runs.error.value" :empty="(runs.data.value ?? []).length === 0" :empty-text="t('runs.empty')" @retry="runs.reload()">
    <AppTable :value="runs.data.value ?? []" data-key="id" data-testid="runs-table">
      <Column field="created_at" :header="t('runs.started')"><template #body="{ data }"><router-link :to="`/runs/${(data as RunListItem).id}`" data-testid="run-open"><RelTime :value="(data as RunListItem).created_at" /></router-link></template></Column>
      <Column field="kind" :header="t('runs.kind.label')"><template #body="{ data }">{{ kindName((data as RunListItem).kind) }}</template></Column>
      <Column field="status" :header="t('runs.status')"><template #body="{ data }"><StatusChip :tone="(data as RunListItem).status === 'done' ? 'ok' : (data as RunListItem).status === 'stopped' ? 'danger' : 'info'" :label="t(`run.status.${(data as RunListItem).status}`)" /></template></Column>
      <Column field="cost_so_far" :header="t('runs.cost')"><template #body="{ data }">{{ usd((data as RunListItem).cost_so_far) }}</template></Column>
    </AppTable>
  </ResourceState>
  </div>
</template>
