<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import Checkbox from 'primevue/checkbox'
import Select from 'primevue/select'
import InputText from 'primevue/inputtext'
import Tree, { type TreeNodeDropEvent } from 'primevue/tree'
import { ApiError, request } from '@/api/client'
import { courseEstimate, courseGenerate, coursePlan, coursePlanSubmit, courseProfile, courseSources } from '@/api/endpoints'
import type { CourseDoc, Estimate, SourceRow } from '@/api/types'
import CourseHeader from '@/components/CourseHeader.vue'
import ActionButton from '@/components/ActionButton.vue'
import AppDialog from '@/components/AppDialog.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import FormField from '@/components/FormField.vue'
import Section from '@/components/Section.vue'
import Queued from '@/components/Queued.vue'
import StatusChip from '@/components/StatusChip.vue'
import { BLOOM_LEVELS, METHODOLOGY_CODES } from '@/composables/methodologies'
import { firstReason, lacking } from '@/composables/reasons'
import { humanize, usd } from '@/composables/labels'
import { useQueued } from '@/composables/useQueued'
import ResourceState from '@/components/ResourceState.vue'
import { notify, notifyError } from '@/composables/notify'
import { applyOps, findTopic, moveTarget, sourceHandles, titleOf, toNodes, toWire, type PlanOp, type PlanTree } from '@/composables/planTree'
import { dropCached, useResource } from '@/composables/useResource'
import { useOperationsStore } from '@/stores/operations'

const { t, te } = useI18n()
const route = useRoute()
const operations = useOperationsStore()
const courseId = computed(() => String(route.params.id))
const header = ref<InstanceType<typeof CourseHeader> | null>(null)

const plan = useResource<PlanTree>(`plan:${courseId.value}`, (signal) => request<PlanTree>(coursePlan(courseId.value), { signal }))
const estimate = useResource<Estimate>(`estimate:${courseId.value}`, (signal) => request<Estimate>(courseEstimate(courseId.value), { signal }))

const sourceRows = useResource<SourceRow[]>(`sources:${courseId.value}`, (signal) => request<SourceRow[]>(courseSources(courseId.value), { signal }))
const noSources = computed(() => sourceRows.data.value !== null && !sourceRows.data.value.some((s) => s.approval === 'approved' && s.progress > 0))
const ops = ref<PlanOp[]>([])
const answers = ref<Record<string, string>>({})
const tree = computed<PlanTree | null>(() => (plan.data.value ? applyOps(plan.data.value, ops.value) : null))
const nodes = computed(() => (tree.value ? toNodes(tree.value) : []))
const dirty = computed(() => ops.value.length > 0)
const selectedKey = ref<string | null>(null)
const selectionKeys = computed({
  get: () => (selectedKey.value ? { [selectedKey.value]: true } : {}),
  set: (v: Record<string, boolean> | undefined) => { selectedKey.value = Object.keys(v ?? {})[0] ?? null },
})
const topic = computed(() => (tree.value && selectedKey.value ? findTopic(tree.value, selectedKey.value) : null))
const selectedTitle = computed(() => (tree.value && selectedKey.value ? titleOf(tree.value, selectedKey.value) : ''))
const isModule = computed(() => Boolean(tree.value?.modules.some((m) => m.id === selectedKey.value)))
const renameText = ref('')
const saving = ref(false)
const saveError = ref<string | null>(null)
const conflict = ref(false)
const confirmDelete = ref(false)
const leaveAsk = ref<((ok: boolean) => void) | null>(null)
const generating = ref(false)
const generateOp = ref<string | null>(null)
const confirmRegenerate = ref(false)
const alreadyGenerated = computed(() => (plan.data.value?.modules ?? []).some((m) => m.topics.some((x) => x.blocks.some((b) => b.status !== 'draft'))))
const narrow = ref(false)
const tab = ref<'plan' | 'before'>('plan')
let media: MediaQueryList | null = null
function onMedia(e: MediaQueryListEvent | MediaQueryList): void { narrow.value = e.matches }

const treePt = {
  nodeToggleButton: ({ context }: { context: { node?: { label?: string } } }) => ({ 'aria-label': t('plan.treeToggle', { name: context?.node?.label ?? '' }) }),
}

function select(key: string | null): void {
  selectedKey.value = key
  renameText.value = tree.value && key ? titleOf(tree.value, key) : ''
}
function stage(op: PlanOp): void { ops.value = [...ops.value, op] }
function rename(): void {
  const title = renameText.value.trim()
  if (!selectedKey.value || !title) return
  stage({ op: isModule.value ? 'rename_module' : 'rename_topic', id: selectedKey.value, title })
}
function onDrop(event: TreeNodeDropEvent): void {
  if (!tree.value) return
  const op = moveTarget(tree.value, String(event.dragNode.key), String(event.dropNode.key))
  if (op) stage(op)
}
function shift(delta: number): void {
  if (!tree.value || !selectedKey.value) return
  const siblings = isModule.value ? tree.value.modules : (tree.value.modules.find((m) => m.topics.some((x) => x.id === selectedKey.value))?.topics ?? [])
  const at = siblings.findIndex((x) => x.id === selectedKey.value)
  const to = at + delta
  if (at < 0 || to < 0 || to >= siblings.length) return
  stage({ op: isModule.value ? 'move_module' : 'move_topic', id: selectedKey.value, ordinal: to })
}
function removeSelected(): void {
  if (!selectedKey.value) return
  stage({ op: isModule.value ? 'delete_module' : 'delete_topic', id: selectedKey.value })
  confirmDelete.value = false
  select(null)
}
async function answer(key: string, course: CourseDoc): Promise<void> {
  const text = (answers.value[key] ?? '').trim()
  if (!text) return
  ops.value = [...ops.value.filter((o) => !(o.op === 'set_constraint' && o.key === key)), { op: 'set_constraint', key, answer: { value: text } }]
  await save(course)
}
const siblingsOfSelected = computed(() => {
  const t0 = tree.value
  if (!t0 || !selectedKey.value) return []
  return isModule.value ? t0.modules : (t0.modules.find((m) => m.topics.some((x) => x.id === selectedKey.value))?.topics ?? [])
})
const position = computed(() => siblingsOfSelected.value.findIndex((x) => x.id === selectedKey.value))
const upReason = computed(() => (position.value <= 0 ? t('plan.alreadyFirst') : null))
const downReason = computed(() => (position.value >= siblingsOfSelected.value.length - 1 ? t('plan.alreadyLast') : null))
const moduleOf = computed(() => (tree.value && selectedKey.value ? (tree.value.modules.find((m) => m.id === selectedKey.value) ?? null) : null))
const blockTypeLabel = (type: string): string => (te(`plan.blockType.${type}`) ? t(`plan.blockType.${type}`) : humanize(type))
const answeredRows = (course: CourseDoc | null): Array<{ label: string; value: string }> =>
  (course?.constraints ?? []).filter((c) => c.answer !== null).map((c) => ({ label: c.question ?? humanize(c.key), value: String((c.answer as Record<string, unknown>).value ?? '') }))

async function save(course: CourseDoc, keepMine = false): Promise<void> {
  saving.value = true
  saveError.value = null
  try {
    const next = await request<PlanTree>(coursePlan(courseId.value), { method: 'PATCH', body: { operations: toWire(plan.data.value ?? { modules: [] }, ops.value), base_version: keepMine ? undefined : course.row_version } })
    plan.data.value = next
    plan.error.value = null
    ops.value = []
    answers.value = {}
    conflict.value = false
    dropCached(`course:${courseId.value}`)
    await header.value?.reload()
    notify('success', t('plan.saved'))
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 409) conflict.value = true
    saveError.value = cause instanceof ApiError ? cause.message : String(cause)
  } finally {
    saving.value = false
  }
}
async function reloadTheirs(): Promise<void> {
  ops.value = []
  conflict.value = false
  saveError.value = null
  await Promise.all([plan.reload(), header.value?.reload()])
}

const methodologies = ref<string[]>([])
const bloom = ref('understand')
const audience = ref('')
const queriesText = ref('')
const building = ref(false)
const planQueue = useQueued(() => `plan:${courseId.value}`, async () => { await Promise.all([plan.reload(true), estimate.reload(true), header.value?.reload()]) })
const genQueue = useQueued(() => `generate:${courseId.value}`, async () => { await header.value?.reload() })
const buildError = ref<string | null>(null)
async function buildPlan(): Promise<void> {
  building.value = true
  buildError.value = null
  try {
    await request(courseProfile(courseId.value), { method: 'POST', body: { methodology_codes: methodologies.value, bloom_level: bloom.value, audience: audience.value.trim() } })
    const made = await request<{ operation_id: string }>(coursePlanSubmit(courseId.value), { method: 'POST', body: { queries: queriesText.value.split('\n').map((q) => q.trim()).filter(Boolean), idempotency_key: crypto.randomUUID() } })
    planQueue.track(made.operation_id)
    await header.value?.reload()
  } catch (cause) {
    buildError.value = cause instanceof ApiError ? cause.message : String(cause)
  } finally {
    building.value = false
  }
}

async function generate(): Promise<void> {
  generating.value = true
  try {
    const made = await request<{ operation_id: string }>(courseGenerate(courseId.value), { method: 'POST', body: { idempotency_key: crypto.randomUUID() } })
    generateOp.value = made.operation_id
    genQueue.track(made.operation_id)
  } catch (cause) {
    notifyError(cause)
  } finally {
    generating.value = false
  }
}
const runId = computed(() => {
  const result = generateOp.value ? operations.items[generateOp.value]?.result : null
  const id = result && typeof result === 'object' ? (result as { run_id?: unknown }).run_id : null
  return typeof id === 'string' ? id : null
})

onBeforeRouteLeave(() => {
  if (!dirty.value) return true
  return new Promise<boolean>((done) => { leaveAsk.value = (ok) => { leaveAsk.value = null; done(ok) } })
})
function warnUnload(event: BeforeUnloadEvent): void { if (dirty.value) event.preventDefault() }
const BEFORE_ANCHORS = /^#(questions|question-.+|generate|build-plan)$/
async function openTabOfHash(hash: string): Promise<void> {
  if (!narrow.value || !hash) return
  tab.value = BEFORE_ANCHORS.test(hash) ? 'before' : 'plan'
  await nextTick()
  document.getElementById(hash.slice(1))?.scrollIntoView()
}
watch(() => route.hash, (hash) => { void openTabOfHash(hash) })
watch(narrow, () => { void openTabOfHash(route.hash) })
onMounted(() => {
  window.addEventListener('beforeunload', warnUnload)
  media = window.matchMedia('(max-width: 1099px)')
  onMedia(media)
  media.addEventListener('change', onMedia)
})
onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', warnUnload)
  media?.removeEventListener('change', onMedia)
})
</script>

<template>
  <CourseHeader ref="header" :course-id="courseId">
    <template #default="{ course, validation }">
      <div class="row">
        <StatusChip v-if="dirty" tone="warn" :label="t('plan.unsaved')" data-testid="unsaved" />
        <ActionButton class="row-end" action="save" :label="t('plan.save')" :loading="saving" :reason="firstReason(lacking('course.edit'), !dirty && t('plan.nothingToSave'))" data-testid="plan-save" @click="course && save(course)" />
      </div>
      <div v-if="conflict" class="confirm-bar stack" role="alert" data-testid="conflict">
        <p>{{ t('plan.conflict') }}</p>
        <div class="row">
          <ActionButton action="reload-theirs" :label="t('plan.reloadTheirs')" data-testid="reload-theirs" @click="reloadTheirs" />
          <ActionButton action="keep-mine" :label="t('plan.keepMine')" data-testid="keep-mine" @click="course && save(course, true)" />
        </div>
      </div>
      <p v-else-if="saveError" class="mono danger-text" role="alert" data-testid="save-error">{{ saveError }}</p>
      <div class="plan-tabs" role="tablist" :aria-label="t('plan.tabs')">
        <button id="tab-plan" type="button" class="plan-tab" role="tab" :aria-selected="tab === 'plan'" aria-controls="plan-centre" @click="tab = 'plan'">{{ t('plan.tabPlan') }}</button>
        <button id="tab-before" type="button" class="plan-tab" role="tab" :aria-selected="tab === 'before'" aria-controls="before-card" @click="tab = 'before'">{{ t('plan.tabBefore') }}</button>
      </div>
      <div class="plan-grid">
        <section class="stack" :aria-label="t('plan.treeLabel')">
          <Section :title="t('plan.treeLabel')" :help="t('plan.treeHelp')" />
          <ResourceState :loading="plan.loading.value" :error="plan.error.value" :empty="nodes.length === 0" :empty-text="t('plan.empty')" @retry="plan.reload()">
            <template #empty-actions><router-link to="#build-plan">{{ t('plan.emptyAction') }}</router-link></template>
            <Tree v-model:selection-keys="selectionKeys" :value="nodes" selection-mode="single" draggable-nodes droppable-nodes draggable-scope="plan" droppable-scope="plan" :pt="treePt" data-testid="plan-tree" @node-select="(n) => select(String(n.key))" @node-drop="onDrop" />
          </ResourceState>
        </section>
        <section v-show="!narrow || tab === 'plan'" id="plan-centre" class="stack" :aria-label="t('plan.topicLabel')" data-testid="plan-centre">
          <Section :title="t('plan.topicLabel')" :help="t('plan.topicHelp')" />
          <p v-if="!selectedKey" class="muted">{{ t('plan.pick') }}</p>
          <template v-else>
            <FormField id="p-title" :label="t('plan.nodeTitle')" :help="t('plan.nodeTitleHint')" required :error="renameText.trim() ? null : t('form.isRequired')">
              <InputText id="p-title" v-model="renameText" @keydown.enter.prevent="rename" />
            </FormField>
            <div class="row">
              <ActionButton action="rename" :label="t('plan.rename')" size="small" :reason="!renameText.trim() || renameText.trim() === selectedTitle ? t('plan.renameFirst') : null" data-testid="plan-rename" @click="rename" />
              <ActionButton action="move-up" :label="t('plan.moveUp')" size="small" :reason="upReason" data-testid="plan-up" @click="shift(-1)" />
              <ActionButton action="move-down" :label="t('plan.moveDown')" size="small" :reason="downReason" data-testid="plan-down" @click="shift(1)" />
              <ActionButton action="delete" :label="t('plan.deleteButton')" size="small" data-testid="plan-delete" @click="confirmDelete = true" />
            </div>
            <template v-if="moduleOf">
              <Section :title="t('plan.purpose')" :help="t('plan.purposeHelp')" />
              <p>{{ moduleOf.purpose }}</p>
              <ul class="plain-list">
                <li v-for="x in moduleOf.topics" :key="x.id"><a href="#" @click.prevent="select(x.id)">{{ x.title }}</a> <span class="count">{{ t('count.block', x.blocks.length) }}</span></li>
              </ul>
            </template>
            <template v-if="topic">
              <Section :title="t('plan.objectives')" :help="t('plan.objectivesHelp')" />
              <ul><li v-for="o in topic.objectives" :key="o">{{ o }}</li></ul>
              <Section :title="t('plan.blocks')" :help="t('plan.blocksHelp')" />
              <ul class="plain-list">
                <li v-for="b in topic.blocks" :key="b.id" class="bell-item stack" data-testid="plan-block">
                  <div class="row">
                    <strong>{{ t(`plan.role.${b.role}`) }}</strong><span class="chip">{{ blockTypeLabel(b.block_type) }}</span>
                    <router-link v-for="h in sourceHandles(b)" :key="h" class="chip mono" :to="`/courses/${courseId}/sources#sources-list`" :title="t('plan.handleTitle', { handle: h })">{{ h }}</router-link>
                  </div>
                  <span>{{ b.brief }}</span>
                </li>
              </ul>
            </template>
          </template>
          <template v-if="answeredRows(course).length > 0">
            <Section :title="t('plan.answered')" :help="t('plan.answeredHelp')" />
            <ul class="plain-list">
              <li v-for="r in answeredRows(course)" :key="r.label" class="bell-item" data-testid="constraint-row"><strong>{{ r.label }}</strong> <span>{{ r.value }}</span></li>
            </ul>
          </template>
        </section>
        <aside v-show="!narrow || tab === 'before'" id="before-card" class="stack" :aria-label="t('plan.before')" data-testid="before-card">
          <Section id="questions" :title="t('plan.before')" :help="t('plan.beforeHelp')" />
          <ResourceState :loading="false" :error="null" :empty="(validation?.blocking_questions ?? []).length === 0" :empty-text="t('plan.noQuestions')">
            <ul class="plain-list">
              <li v-for="q in validation?.blocking_questions ?? []" :id="`question-${q.key}`" :key="q.key" class="bell-item stack" data-testid="question">
                <FormField :id="`q-${q.key}`" :label="q.question ?? humanize(q.key)" :help="t('plan.answerHint')">
                  <InputText :id="`q-${q.key}`" v-model="answers[q.key]" />
                </FormField>
                <ActionButton action="save" :label="t('plan.answer')" size="small" :loading="saving" :reason="firstReason(lacking('course.edit'), !(answers[q.key] ?? '').trim() && t('plan.typeAnswer'))" @click="course && answer(q.key, course)" />
              </li>
            </ul>
          </ResourceState>
          <Section :title="t('plan.estimate')" :help="t('plan.forecastNote')" />
          <ResourceState :loading="estimate.loading.value" :error="estimate.error.value" :empty="false" @retry="estimate.reload()">
            <p data-testid="forecast">{{ t('plan.forecastLine', { blocks: t('count.block', estimate.data.value?.blocks ?? 0), cost: usd(estimate.data.value?.cost_usd ?? '0') }) }}</p>
          </ResourceState>
          <Section id="generate" :title="t('plan.generateTitle')" :help="t('plan.generateHelp')" />
          <ActionButton action="generate" :label="t('plan.generate')" :loading="generating" :reason="firstReason(lacking('run.start'), dirty && t('plan.saveFirst'), validation && !validation.can_generate && t('plan.questionsOpen'), noSources && t('plan.addSourceFirst'), genQueue.queued.value && t('plan.alreadyQueued'))" data-testid="generate" @click="alreadyGenerated ? (confirmRegenerate = true) : generate()" />
          <router-link v-if="noSources" :to="`/courses/${courseId}/sources`" data-testid="go-sources-generate">{{ t('plan.goSources') }}</router-link>
          <Queued v-if="genQueue.queued.value" />
          <router-link v-if="runId" :to="`/runs/${runId}`" data-testid="open-run">{{ t('plan.openRun') }}</router-link>
          <template v-if="!course?.profile || nodes.length === 0">
            <Section id="build-plan" :title="t('plan.build')" :help="t('plan.buildHelp')" />
            <form class="stack" @submit.prevent="buildPlan">
              <FormField id="b-meth" group :label="t('plan.methodology')" :help="t('plan.methodologyHint')" required :error="methodologies.length === 0 ? t('plan.pickMethodology') : null">
                <div v-for="code in METHODOLOGY_CODES" :key="code" class="perm-check">
                  <Checkbox v-model="methodologies" :input-id="`m-${code}`" :value="code" />
                  <label :for="`m-${code}`"><strong>{{ t(`methodology.${code}.name`) }}</strong> <span class="hint">{{ t(`methodology.${code}.line`) }}</span></label>
                </div>
              </FormField>
              <FormField id="b-bloom" :label="t('plan.bloom')" :help="t('plan.bloomHint')"><Select v-model="bloom" input-id="b-bloom" :options="BLOOM_LEVELS.map((c) => ({ code: c, label: t(`bloom.${c}`) }))" option-label="label" option-value="code" /></FormField>
              <FormField id="b-aud" :label="t('plan.audience')" :help="t('plan.audienceHint')"><InputText id="b-aud" v-model="audience" /></FormField>
              <FormField id="b-q" :label="t('plan.queries')" :help="t('plan.queriesHint')" required :error="queriesText.trim().length === 0 ? t('plan.typeQueries') : null"><textarea id="b-q" v-model="queriesText" rows="3"></textarea></FormField>
              <router-link v-if="noSources" :to="`/courses/${courseId}/sources`" data-testid="go-sources">{{ t('plan.goSources') }}</router-link>
              <p v-if="buildError" class="mono danger-text" role="alert" data-testid="build-error">{{ buildError }}</p>
              <ActionButton type="submit" action="build-plan" :label="t('plan.buildButton')" :loading="building" :reason="firstReason(lacking('course.plan'), noSources && t('plan.addSourceFirst'), methodologies.length === 0 && t('plan.pickMethodology'), queriesText.trim().length === 0 && t('plan.typeQueries'), planQueue.queued.value && t('plan.alreadyQueued'))" data-testid="plan-build" />
              <Queued v-if="planQueue.queued.value" />
            </form>
          </template>
        </aside>
      </div>
      <ConfirmDialog v-model:visible="confirmRegenerate" :title="t('plan.regenerateTitle')" :body="t('plan.regenerateBody', { cost: usd(estimate.data.value?.cost_usd ?? '0') })" action="generate" :label="t('plan.generate')" :solid="false" @confirm="() => { confirmRegenerate = false; void generate() }" />
      <ConfirmDialog v-model:visible="confirmDelete" :title="t('plan.deleteTitle', { name: selectedTitle })" :body="t('plan.deleteBody', { name: selectedTitle })" action="delete" :label="t('plan.deleteButton')" @confirm="removeSelected" />
      <AppDialog :visible="leaveAsk !== null" :closable="false" :header="t('common.unsavedTitle')" size="sm">
        <p>{{ t('common.unsavedBody') }}</p>
        <div class="row">
          <ActionButton action="keep-editing" data-testid="leave-stay" @click="leaveAsk?.(false)" />
          <ActionButton action="discard" solid data-testid="leave-go" @click="leaveAsk?.(true)" />
        </div>
      </AppDialog>
    </template>
  </CourseHeader>
</template>
