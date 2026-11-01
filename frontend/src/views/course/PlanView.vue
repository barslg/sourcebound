<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { ChevronDown, ChevronRight } from 'lucide-vue-next'
import InputText from 'primevue/inputtext'
import { ApiError, request } from '@/api/client'
import { courseItem, coursePlan, coursePlanSubmit, courseProfile, courseRuns, courseSources } from '@/api/endpoints'
import type { CourseDoc, RunListItem, SourceRow } from '@/api/types'
import CourseHeader from '@/components/CourseHeader.vue'
import ActionButton from '@/components/ActionButton.vue'
import AppDialog from '@/components/AppDialog.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import FormField from '@/components/FormField.vue'
import PlanBuildForm from '@/components/PlanBuildForm.vue'
import Section from '@/components/Section.vue'
import ResourceState from '@/components/ResourceState.vue'
import RowMenu, { type RowMenuItem } from '@/components/RowMenu.vue'
import { firstReason, lacking } from '@/composables/reasons'
import { humanize } from '@/composables/labels'
import { useQueued } from '@/composables/useQueued'
import { sourceHandles, type PlanModule, type PlanOp, type PlanTopic, type PlanTree } from '@/composables/planTree'
import { dropCached, useResource } from '@/composables/useResource'

const { t, te } = useI18n()
const route = useRoute()
const courseId = computed(() => String(route.params.id))
const header = ref<InstanceType<typeof CourseHeader> | null>(null)

const plan = useResource<PlanTree>(`plan:${courseId.value}`, (signal) => request<PlanTree>(coursePlan(courseId.value), { signal }))
const courseRes = useResource<CourseDoc>(`course:${courseId.value}`, (signal) => request<CourseDoc>(courseItem(courseId.value), { signal }))
const sourceRows = useResource<SourceRow[]>(`sources:${courseId.value}`, (signal) => request<SourceRow[]>(courseSources(courseId.value), { signal }))
const noSources = computed(() => sourceRows.data.value !== null && !sourceRows.data.value.some((s) => s.approval === 'approved' && s.progress > 0))
const isEmpty = computed(() => plan.data.value !== null && plan.data.value.modules.length === 0)

/* Autosave: every edit is one PATCH with the course row_version we last saw. */
const version = ref<number | null>(null)
watch(() => courseRes.data.value?.row_version, (v) => { if (v !== undefined && status.value !== 'saving') version.value = v })
type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'
const status = ref<SaveStatus>('idle')
const saveMessage = ref('')
const conflict = ref(false)
const pendingOps = ref<PlanOp[]>([])

async function apply(ops: PlanOp[], base: number | null = null): Promise<PlanTree | null> {
  status.value = 'saving'
  saveMessage.value = ''
  try {
    const next = await request<PlanTree>(coursePlan(courseId.value), { method: 'PATCH', body: { operations: ops, base_version: base ?? version.value ?? undefined } })
    plan.data.value = next
    plan.error.value = null
    if (typeof next.row_version === 'number') version.value = next.row_version
    status.value = 'saved'
    conflict.value = false
    pendingOps.value = []
    dropCached(`course:${courseId.value}`)
    await header.value?.reload()
    return next
  } catch (cause) {
    pendingOps.value = ops
    if (cause instanceof ApiError && cause.status === 409) { conflict.value = true; status.value = 'idle' }
    else { status.value = 'error'; saveMessage.value = cause instanceof ApiError ? cause.message : String(cause) }
    return null
  }
}
async function reloadTheirs(): Promise<void> {
  conflict.value = false
  pendingOps.value = []
  status.value = 'idle'
  await Promise.all([plan.reload(true), courseRes.reload(true), header.value?.reload()])
}
async function keepMine(): Promise<void> {
  await courseRes.reload(true)
  const current = courseRes.data.value?.row_version
  await apply(pendingOps.value, typeof current === 'number' ? current : null)
}

/* Inline title editing: the draft lives beside the tree and is sent on blur. */
const titles = ref<Record<string, string>>({})
const editingId = ref<string | null>(null)
watch(plan.data, (tree) => {
  if (!tree) return
  const next: Record<string, string> = {}
  for (const m of tree.modules) {
    next[m.id] = editingId.value === m.id ? (titles.value[m.id] ?? m.title) : m.title
    for (const x of m.topics) next[x.id] = editingId.value === x.id ? (titles.value[x.id] ?? x.title) : x.title
  }
  titles.value = next
}, { immediate: true })

async function commitTitle(kind: 'module' | 'topic', id: string, current: string): Promise<void> {
  editingId.value = null
  const title = (titles.value[id] ?? '').trim()
  if (!title || title === current) { titles.value = { ...titles.value, [id]: current }; return }
  await apply([{ op: kind === 'module' ? 'rename_module' : 'rename_topic', id, title }])
}
function focusTitle(id: string): void {
  editingId.value = id
  void nextTick(() => {
    const el = document.getElementById(`p-title-${id}`) as HTMLInputElement | null
    el?.focus()
    el?.select()
  })
}

const openTopics = ref<ReadonlySet<string>>(new Set())
function toggleTopic(id: string): void {
  const next = new Set(openTopics.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  openTopics.value = next
}

const deleteAsk = ref<{ kind: 'module' | 'topic'; id: string; name: string } | null>(null)
async function confirmDelete(): Promise<void> {
  const ask = deleteAsk.value
  deleteAsk.value = null
  if (ask) await apply([{ op: ask.kind === 'module' ? 'delete_module' : 'delete_topic', id: ask.id }])
}

function moduleMenu(m: PlanModule, index: number): RowMenuItem[] {
  const modules = plan.data.value?.modules ?? []
  return [
    { action: 'rename', label: t('plan.rename'), run: () => focusTitle(m.id) },
    { action: 'move-up', label: t('plan.moveUp'), reason: index <= 0 ? t('plan.alreadyFirst') : null, run: () => void apply([{ op: 'move_module', id: m.id, ordinal: index - 1 }]) },
    { action: 'move-down', label: t('plan.moveDown'), reason: index >= modules.length - 1 ? t('plan.alreadyLast') : null, run: () => void apply([{ op: 'move_module', id: m.id, ordinal: index + 1 }]) },
    { action: 'add-topic', label: t('plan.addTopic'), run: () => void addTopic(m, m.topics.length - 1) },
    { action: 'delete', label: t('plan.deleteButton'), run: () => { deleteAsk.value = { kind: 'module', id: m.id, name: m.title } } },
  ]
}
function topicMenu(m: PlanModule, x: PlanTopic, index: number): RowMenuItem[] {
  return [
    { action: 'rename', label: t('plan.rename'), run: () => focusTitle(x.id) },
    { action: 'move-up', label: t('plan.moveUp'), reason: index <= 0 ? t('plan.alreadyFirst') : null, run: () => void apply([{ op: 'move_topic', id: x.id, ordinal: index - 1 }]) },
    { action: 'move-down', label: t('plan.moveDown'), reason: index >= m.topics.length - 1 ? t('plan.alreadyLast') : null, run: () => void apply([{ op: 'move_topic', id: x.id, ordinal: index + 1 }]) },
    { action: 'add-topic', label: t('plan.addTopicBelow'), run: () => void addTopic(m, index) },
    { action: 'delete', label: t('plan.deleteButton'), run: () => { deleteAsk.value = { kind: 'topic', id: x.id, name: x.title } } },
  ]
}

async function addModule(): Promise<void> {
  const next = await apply([{ op: 'add_module', title: t('plan.newModule') }])
  const last = next?.modules[next.modules.length - 1]
  if (last) focusTitle(last.id)
}
async function addTopic(m: PlanModule, afterIndex: number): Promise<void> {
  const next = await apply([{ op: 'add_topic', module_id: m.id, title: t('plan.newTopic'), kind: 'lesson' }])
  const mod = next?.modules.find((x) => x.id === m.id)
  const added = mod?.topics[mod.topics.length - 1]
  if (!mod || !added) return
  if (mod.topics.length - 1 !== afterIndex + 1) {
    const moved = await apply([{ op: 'move_topic', id: added.id, ordinal: afterIndex + 1 }])
    if (!moved) return
  }
  openTopics.value = new Set(openTopics.value).add(added.id)
  focusTitle(added.id)
}

const answers = ref<Record<string, string>>({})
const answering = ref<string | null>(null)
async function answer(key: string): Promise<void> {
  const text = (answers.value[key] ?? '').trim()
  if (!text) return
  answering.value = key
  await apply([{ op: 'set_constraint', key, answer: { value: text } }])
  answering.value = null
}

const methodologies = ref<string[]>([])
const queriesText = ref('')
const building = ref(false)
const buildError = ref<string | null>(null)
const rebuildOpen = ref(false)
const runs = useResource<RunListItem[]>(`runs:${courseId.value}`, (signal) => request<RunListItem[]>(courseRuns(courseId.value), { signal }))
const latestPlanRun = computed(() => (runs.data.value ?? []).find((r) => r.kind === 'plan') ?? null)
const planQueue = useQueued(() => `plan:${courseId.value}`, async () => { await Promise.all([plan.reload(true), runs.reload(true), header.value?.reload()]) })
async function buildPlan(): Promise<void> {
  building.value = true
  buildError.value = null
  try {
    await request(courseProfile(courseId.value), { method: 'POST', body: { methodology_codes: methodologies.value, bloom_level: 'understand', audience: courseRes.data.value?.profile?.audience ?? '' } })
    const made = await request<{ operation_id: string }>(coursePlanSubmit(courseId.value), { method: 'POST', body: { queries: queriesText.value.split('\n').map((q) => q.trim()).filter(Boolean), idempotency_key: crypto.randomUUID() } })
    planQueue.track(made.operation_id)
    await header.value?.reload()
  } catch (cause) {
    buildError.value = cause instanceof ApiError ? cause.message : String(cause)
  } finally {
    building.value = false
  }
}

const blockTypeLabel = (type: string): string => (te(`plan.blockType.${type}`) ? t(`plan.blockType.${type}`) : humanize(type))
const kindLabel = (kind: string): string => (te(`plan.kind.${kind}`) ? t(`plan.kind.${kind}`) : humanize(kind))
function sourceChipLabel(handle: string): string {
  for (const m of plan.data.value?.modules ?? []) {
    for (const x of m.topics) {
      for (const b of x.blocks) {
        const ref = (b.source_refs ?? []).find((r) => r.handle === handle)
        const title = ref?.source_id ? sourceRows.data.value?.find((s) => s.id === ref.source_id)?.title : undefined
        if (title) return `${title} ${handle}`
      }
    }
  }
  return handle
}

watch(() => route.hash, (hash) => {
  if (!hash) return
  void nextTick(() => document.getElementById(hash.slice(1))?.scrollIntoView())
}, { immediate: true })
</script>

<template>
  <CourseHeader ref="header" :course-id="courseId">
    <template #default="{ validation }">
      <p class="save-line" role="status" data-testid="save-status">
        <span v-if="status === 'saving'">{{ t('plan.saving') }}</span>
        <span v-else-if="status === 'error'" class="danger-text">{{ t('plan.saveFailed', { message: saveMessage }) }}</span>
        <span v-else-if="status === 'saved'">{{ t('plan.savedLine') }}</span>
        <ActionButton v-if="status === 'error'" action="retry" size="small" data-testid="save-retry" @click="apply(pendingOps)" />
      </p>

      <section v-if="(validation?.blocking_questions ?? []).length > 0" id="questions" class="stack" :aria-label="t('plan.before')" data-testid="before-card">
        <Section :title="t('plan.before')" :help="t('plan.beforeHelp')" />
        <ul class="plain-list">
          <li v-for="q in validation?.blocking_questions ?? []" :id="`question-${q.key}`" :key="q.key" class="bell-item stack" data-testid="question">
            <FormField :id="`q-${q.key}`" :label="q.question ?? humanize(q.key)" :help="t('plan.answerHint')">
              <InputText :id="`q-${q.key}`" v-model="answers[q.key]" />
            </FormField>
            <div>
              <ActionButton action="save" :label="t('plan.answer')" size="small" :loading="answering === q.key" :reason="firstReason(lacking('course.edit'), !(answers[q.key] ?? '').trim() && t('plan.typeAnswer'))" data-testid="answer-save" @click="answer(q.key)" />
            </div>
          </li>
        </ul>
      </section>

      <ResourceState :loading="plan.loading.value" :error="plan.error.value" :empty="isEmpty" :empty-text="t('plan.buildCard')" @retry="plan.reload()">
        <template #empty-actions>
          <p v-if="latestPlanRun?.status === 'stopped'" class="mono danger-text" role="alert" data-testid="plan-stopped">{{ latestPlanRun.stop_marker ?? t('plan.buildStopped') }}</p>
          <PlanBuildForm v-model:methodologies="methodologies" v-model:queries-text="queriesText" :course-id="courseId" form-id="build-plan" field-prefix="b" choice-prefix="m" card-testid="build-card" submit-testid="plan-build" :submit-label="latestPlanRun?.status === 'stopped' ? t('plan.buildAgain') : t('plan.buildButton')" :building="building" :build-error="buildError" :no-sources="noSources" :op-queued="planQueue.queued.value" @submit="buildPlan" />
        </template>

        <section v-if="latestPlanRun?.status === 'stopped'" class="stack" data-testid="plan-stopped">
          <Section :title="t('plan.stoppedTitle')" :help="t('plan.stoppedHelp')" />
          <p class="mono danger-text" role="alert">{{ latestPlanRun.stop_marker ?? t('plan.buildStopped') }}</p>
          <div>
            <ActionButton action="build-plan" :label="t('plan.buildAgain')" :reason="lacking('course.plan')" data-testid="plan-rebuild" @click="rebuildOpen = true" />
          </div>
          <PlanBuildForm v-if="rebuildOpen" v-model:methodologies="methodologies" v-model:queries-text="queriesText" :course-id="courseId" form-id="rebuild-plan" field-prefix="rb" choice-prefix="rb-m" card-testid="rebuild-card" submit-testid="plan-rebuild-submit" :submit-label="t('plan.buildAgain')" :building="building" :build-error="buildError" :no-sources="noSources" :op-queued="planQueue.queued.value" @submit="buildPlan" />
        </section>

        <section class="stack" :aria-label="t('plan.outline')">
          <Section :title="t('plan.outline')" :help="t('plan.outlineHelp')" />
          <div class="stack" data-testid="plan-outline">
            <article v-for="(m, mi) in plan.data.value?.modules ?? []" :key="m.id" class="module-card stack" data-testid="plan-module">
              <div class="row module-head">
                <InputText :id="`p-title-${m.id}`" v-model="titles[m.id]" class="title-input module-title" :aria-label="t('plan.titleOf', { name: m.title })" data-testid="module-title" @focus="editingId = m.id" @blur="commitTitle('module', m.id, m.title)" @keydown.enter.prevent="($event.target as HTMLInputElement).blur()" />
                <span class="count">{{ t('count.topic', m.topics.length) }}</span>
                <span class="row-end"><RowMenu :label="t('plan.moreFor', { name: m.title })" :items="moduleMenu(m, mi)" /></span>
              </div>
              <ul class="plain-list">
                <li v-for="(x, xi) in m.topics" :key="x.id" class="topic-block" data-testid="plan-topic">
                  <div class="row topic-row">
                    <button type="button" class="disclosure" :aria-expanded="openTopics.has(x.id)" :aria-controls="`blocks-${x.id}`" :aria-label="t('plan.topicToggle', { name: x.title })" data-testid="topic-toggle" @click="toggleTopic(x.id)">
                      <ChevronDown v-if="openTopics.has(x.id)" :size="16" :stroke-width="2" aria-hidden="true" />
                      <ChevronRight v-else :size="16" :stroke-width="2" aria-hidden="true" />
                    </button>
                    <InputText :id="`p-title-${x.id}`" v-model="titles[x.id]" class="title-input" :aria-label="t('plan.titleOf', { name: x.title })" data-testid="topic-title" @focus="editingId = x.id" @blur="commitTitle('topic', x.id, x.title)" @keydown.enter.prevent="($event.target as HTMLInputElement).blur()" />
                    <span class="count">{{ t('count.block', x.blocks.length) }} · {{ kindLabel(x.kind) }}</span>
                    <span class="row-end"><RowMenu :label="t('plan.moreFor', { name: x.title })" :items="topicMenu(m, x, xi)" /></span>
                  </div>
                  <div v-if="openTopics.has(x.id)" :id="`blocks-${x.id}`" class="topic-blocks">
                    <ul v-if="x.objectives.length > 0" class="objectives"><li v-for="o in x.objectives" :key="o">{{ o }}</li></ul>
                    <ul class="plain-list">
                      <li v-for="b in x.blocks" :key="b.id" class="bell-item stack" data-testid="plan-block">
                        <div class="row">
                          <strong>{{ t(`plan.role.${b.role}`) }}</strong><span class="chip">{{ blockTypeLabel(b.block_type) }}</span>
                          <router-link v-for="h in sourceHandles(b)" :key="h" class="chip mono" :to="`/courses/${courseId}/sources#passage-${h}`" :title="t('plan.handleTitle', { handle: h })">{{ sourceChipLabel(h) }}</router-link>
                        </div>
                        <span>{{ b.brief }}</span>
                      </li>
                    </ul>
                    <p v-if="x.blocks.length === 0" class="muted" data-testid="topic-tree-error">{{ t('plan.emptyTopic') }}</p>
                  </div>
                </li>
              </ul>
            </article>
          </div>
          <div>
            <ActionButton action="add-module" :label="t('plan.addModule')" :reason="lacking('course.edit')" data-testid="plan-add-module" @click="addModule" />
          </div>
        </section>

        <section v-if="(plan.data.value?.through_line?.handoffs ?? []).length > 0" class="stack" data-testid="handoffs">
          <p v-for="h in plan.data.value?.through_line?.handoffs ?? []" :key="h.topic_plan_node_id" class="muted">{{ t('plan.handoffNext', { note: h.note }) }}</p>
        </section>
        <section v-if="(plan.data.value?.uncovered ?? []).length > 0" class="stack" data-testid="not-used">
          <p class="muted">{{ t('plan.notUsed', { list: (plan.data.value?.uncovered ?? []).map((u) => `${u.address || u.handle}${u.excerpt ? ` «${u.excerpt}»` : ''} — ${u.reason}`).join('; ') }) }}</p>
        </section>

        <section class="next-card" data-testid="next-step">
          <Section :title="t('plan.nextTitle')" :help="t('plan.nextHelp')" />
          <router-link v-if="(validation?.blocking_questions ?? []).length === 0 && (validation?.tree_errors ?? []).length === 0" class="link-button" :to="`/courses/${courseId}/generate`" data-testid="next-generate">{{ t('plan.nextOpen') }}</router-link>
          <p v-else-if="(validation?.blocking_questions ?? []).length > 0" class="hint" data-testid="next-reason">{{ t('plan.questionsOpen') }}</p>
          <p v-else class="hint" data-testid="next-reason">{{ t('plan.treeInvalid') }}</p>
        </section>
      </ResourceState>

      <AppDialog :visible="conflict" :closable="false" :header="t('plan.conflictTitle')" size="sm">
        <div class="stack" data-testid="conflict">
          <p>{{ t('plan.conflictBody') }}</p>
          <div class="row">
            <ActionButton action="reload-theirs" :label="t('plan.reloadTheirs')" data-testid="reload-theirs" @click="reloadTheirs" />
            <ActionButton action="keep-mine" :label="t('plan.keepMine')" data-testid="keep-mine" @click="keepMine" />
          </div>
        </div>
      </AppDialog>
      <ConfirmDialog :visible="deleteAsk !== null" :title="t('plan.deleteTitle', { name: deleteAsk?.name ?? '' })" :body="t('plan.deleteBody', { name: deleteAsk?.name ?? '' })" action="delete" :label="t('plan.deleteButton')" :typed="deleteAsk?.name ?? null" @update:visible="(v: boolean) => { if (!v) deleteAsk = null }" @confirm="confirmDelete" />
    </template>
  </CourseHeader>
</template>
