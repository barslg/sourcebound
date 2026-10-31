<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Tree, { type TreeNodeDropEvent } from 'primevue/tree'
import { ApiError, request } from '@/api/client'
import { courseEstimate, courseGenerate, coursePlan, coursePlanSubmit, courseProfile } from '@/api/endpoints'
import type { CourseDoc, Estimate } from '@/api/types'
import CourseHeader from '@/components/CourseHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import { notify } from '@/composables/notify'
import { applyOps, findTopic, moveTarget, sourceHandles, titleOf, toNodes, toWire, type PlanOp, type PlanTree } from '@/composables/planTree'
import { parseTags } from '@/composables/tags'
import { dropCached, useResource } from '@/composables/useResource'
import { useOperationsStore } from '@/stores/operations'
import { useSessionStore } from '@/stores/session'

const { t } = useI18n()
const route = useRoute()
const session = useSessionStore()
const operations = useOperationsStore()
const courseId = computed(() => String(route.params.id))
const header = ref<InstanceType<typeof CourseHeader> | null>(null)

const plan = useResource<PlanTree>(`plan:${courseId.value}`, (signal) => request<PlanTree>(coursePlan(courseId.value), { signal }))
const estimate = useResource<Estimate>(`estimate:${courseId.value}`, (signal) => request<Estimate>(courseEstimate(courseId.value), { signal }))

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
function answer(key: string): void {
  const text = (answers.value[key] ?? '').trim()
  if (!text) return
  ops.value = [...ops.value.filter((o) => !(o.op === 'set_constraint' && o.key === key)), { op: 'set_constraint', key, answer: { value: text } }]
}
function staged(key: string): boolean {
  return ops.value.some((o) => o.op === 'set_constraint' && o.key === key)
}

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

const profileCodes = ref('')
const audience = ref('')
const queriesText = ref('')
const building = ref(false)
const buildError = ref<string | null>(null)
async function buildPlan(): Promise<void> {
  building.value = true
  buildError.value = null
  try {
    await request(courseProfile(courseId.value), { method: 'POST', body: { methodology_codes: parseTags(profileCodes.value), audience: audience.value.trim() } })
    await request(coursePlanSubmit(courseId.value), { method: 'POST', body: { queries: queriesText.value.split('\n').map((q) => q.trim()).filter(Boolean), idempotency_key: crypto.randomUUID() } })
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
  } catch (cause) {
    notify('error', cause instanceof ApiError ? cause.message : String(cause))
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
onMounted(() => window.addEventListener('beforeunload', warnUnload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', warnUnload))
</script>

<template>
  <CourseHeader ref="header" :course-id="courseId">
    <template #default="{ course, validation }">
      <div class="row">
        <span v-if="dirty" class="chip chip-warn" data-testid="unsaved">{{ t('plan.unsaved') }}</span>
        <Button class="row-end" :label="t('plan.save')" :loading="saving" :aria-busy="saving" :disabled="!dirty || !course || !session.can('course.edit')" data-testid="plan-save" @click="course && save(course)" />
      </div>
      <div v-if="conflict" class="confirm-bar stack" role="alert" data-testid="conflict">
        <p>{{ t('plan.conflict') }}</p>
        <div class="row">
          <Button :label="t('plan.reloadTheirs')" severity="secondary" data-testid="reload-theirs" @click="reloadTheirs" />
          <Button :label="t('plan.keepMine')" severity="secondary" data-testid="keep-mine" @click="course && save(course, true)" />
        </div>
      </div>
      <p v-else-if="saveError" class="mono danger-text" role="alert" data-testid="save-error">{{ saveError }}</p>
      <div class="plan-grid">
        <section class="stack" :aria-label="t('plan.treeLabel')">
          <ResourceState :loading="plan.loading.value" :error="plan.error.value" :empty="nodes.length === 0" :empty-text="t('plan.empty')" @retry="plan.reload()">
            <Tree v-model:selection-keys="selectionKeys" :value="nodes" selection-mode="single" draggable-nodes droppable-nodes draggable-scope="plan" droppable-scope="plan" :pt="treePt" data-testid="plan-tree" @node-select="(n) => select(String(n.key))" @node-drop="onDrop" />
          </ResourceState>
        </section>
        <section class="stack" :aria-label="t('plan.topicLabel')" data-testid="plan-centre">
          <p v-if="!selectedKey" class="muted">{{ t('plan.pick') }}</p>
          <template v-else>
            <div class="field">
              <label for="p-title">{{ t('plan.nodeTitle') }}</label>
              <InputText id="p-title" v-model="renameText" @keydown.enter.prevent="rename" />
            </div>
            <div class="row">
              <Button :label="t('plan.rename')" size="small" :disabled="!renameText.trim() || renameText.trim() === selectedTitle" data-testid="plan-rename" @click="rename" />
              <Button :label="t('plan.moveUp')" size="small" severity="secondary" data-testid="plan-up" @click="shift(-1)" />
              <Button :label="t('plan.moveDown')" size="small" severity="secondary" data-testid="plan-down" @click="shift(1)" />
              <Button :label="t('common.delete')" size="small" severity="danger" outlined data-testid="plan-delete" @click="confirmDelete = true" />
            </div>
            <template v-if="topic">
              <h2>{{ t('plan.objectives') }}</h2>
              <ul><li v-for="o in topic.objectives" :key="o">{{ o }}</li></ul>
              <h2>{{ t('plan.blocks') }}</h2>
              <ul class="plain-list">
                <li v-for="b in topic.blocks" :key="b.id" class="bell-item stack" data-testid="plan-block">
                  <div class="row">
<strong>{{ t(`plan.role.${b.role}`) }}</strong><span class="chip mono">{{ b.block_type }}</span>
                    <span v-for="h in sourceHandles(b)" :key="h" class="chip mono">{{ h }}</span>
</div>
                  <span>{{ b.brief }}</span>
                </li>
              </ul>
            </template>
          </template>
          <h2>{{ t('plan.constraints') }}</h2>
          <p v-if="(course?.constraints ?? []).length === 0" class="muted">{{ t('plan.noConstraints') }}</p>
          <ul class="plain-list">
            <li v-for="c in course?.constraints ?? []" :key="c.key" class="bell-item row" data-testid="constraint-row">
              <span class="mono">{{ c.key }}</span><span class="chip">{{ c.kind }}</span>
              <span v-if="c.required" class="chip chip-warn">{{ t('plan.required') }}</span>
              <span class="muted">{{ c.answer ? JSON.stringify(c.answer) : t('plan.unanswered') }}</span>
            </li>
          </ul>
        </section>
        <aside class="stack" :aria-label="t('plan.before')" data-testid="before-card">
          <h2>{{ t('plan.before') }}</h2>
          <ResourceState :loading="false" :error="null" :empty="(validation?.blocking_questions ?? []).length === 0" :empty-text="t('plan.noQuestions')">
            <ul class="plain-list">
              <li v-for="q in validation?.blocking_questions ?? []" :key="q.key" class="bell-item stack" data-testid="question">
                <label :for="`q-${q.key}`">{{ q.question ?? q.key }}</label>
                <InputText :id="`q-${q.key}`" v-model="answers[q.key]" />
                <span class="hint">{{ t('plan.answerHint') }}</span>
                <div class="row">
                  <Button :label="t('plan.answer')" size="small" :disabled="!(answers[q.key] ?? '').trim()" @click="answer(q.key)" />
                  <span v-if="staged(q.key)" class="chip chip-warn">{{ t('plan.answerStaged') }}</span>
                </div>
              </li>
            </ul>
          </ResourceState>
          <h3>{{ t('plan.forecast') }}</h3>
          <ResourceState :loading="estimate.loading.value" :error="estimate.error.value" :empty="false" @retry="estimate.reload()">
            <p data-testid="forecast">{{ t('plan.forecastLine', { blocks: estimate.data.value?.blocks ?? 0, cost: estimate.data.value?.cost_usd ?? '0' }) }}</p>
          </ResourceState>
          <Button :label="t('plan.generate')" :loading="generating" :aria-busy="generating" :disabled="!validation?.can_generate || dirty || !session.can('run.start')" data-testid="generate" @click="generate" />
          <p v-if="dirty || (validation && !validation.can_generate)" class="hint">{{ dirty ? t('plan.saveFirst') : t('plan.questionsOpen') }}</p>
          <router-link v-if="runId" :to="`/runs/${runId}`" data-testid="open-run">{{ t('plan.openRun') }}</router-link>
          <h3 v-if="!course?.profile || nodes.length === 0">{{ t('plan.build') }}</h3>
          <form v-if="!course?.profile || nodes.length === 0" class="stack" @submit.prevent="buildPlan">
            <div class="field"><label for="b-code">{{ t('plan.methodology') }}</label><InputText id="b-code" v-model="profileCodes" /><span class="hint">{{ t('plan.methodologyHint') }}</span></div>
            <div class="field"><label for="b-aud">{{ t('plan.audience') }}</label><InputText id="b-aud" v-model="audience" /><span class="hint">{{ t('plan.audienceHint') }}</span></div>
            <div class="field"><label for="b-q">{{ t('plan.queries') }}</label><textarea id="b-q" v-model="queriesText" rows="3"></textarea><span class="hint">{{ t('plan.queriesHint') }}</span></div>
            <p v-if="buildError" class="mono danger-text" role="alert" data-testid="build-error">{{ buildError }}</p>
            <Button type="submit" :label="t('plan.buildButton')" :loading="building" :aria-busy="building" :disabled="parseTags(profileCodes).length === 0 || queriesText.trim().length === 0" data-testid="plan-build" />
          </form>
        </aside>
      </div>
      <Dialog v-model:visible="confirmDelete" modal :header="t('plan.deleteTitle', { name: selectedTitle })" :style="{ width: 'min(460px, 92vw)' }">
        <p>{{ t('plan.deleteBody', { name: selectedTitle }) }}</p>
        <div class="row">
          <Button :label="t('common.delete')" severity="danger" data-testid="dialog-submit" @click="removeSelected" />
          <Button :label="t('common.cancel')" severity="secondary" text @click="confirmDelete = false" />
        </div>
      </Dialog>
      <Dialog :visible="leaveAsk !== null" modal :closable="false" :header="t('common.unsavedTitle')" :style="{ width: 'min(420px, 92vw)' }">
        <p>{{ t('common.unsavedBody') }}</p>
        <div class="row">
          <Button :label="t('common.keepEditing')" severity="secondary" data-testid="leave-stay" @click="leaveAsk?.(false)" />
          <Button :label="t('common.discard')" severity="danger" data-testid="leave-go" @click="leaveAsk?.(true)" />
        </div>
      </Dialog>
    </template>
  </CourseHeader>
</template>
