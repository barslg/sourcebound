<script setup lang="ts">
import { computed, nextTick, provide, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { request } from '@/api/client'
import { courseCallTree, courseItem } from '@/api/endpoints'
import type { CallTreeNode, CourseDoc } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import BuildFlow from '@/components/BuildFlow.vue'
import CallDetailDrawer from '@/components/CallDetailDrawer.vue'
import ChainNode from '@/components/ChainNode.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import SearchField from '@/components/SearchField.vue'
import StatTiles from '@/components/StatTiles.vue'
import { CHAIN_CONTEXT } from '@/composables/chainContext'
import { filterTree, indexTree, pathTo } from '@/composables/chainTree'
import { usdFine } from '@/composables/labels'
import { useResource } from '@/composables/useResource'

const { t } = useI18n()
const route = useRoute()
const id = computed(() => String(route.params.id))
const course = useResource<CourseDoc>(`course:${id.value}`, (signal) => request<CourseDoc>(courseItem(id.value), { signal }))
const tree = useResource<CallTreeNode>(`chains:${id.value}`, (signal) => request<CallTreeNode>(courseCallTree(id.value), { signal }))
const root = computed(() => tree.data.value)
const index = computed(() => indexTree(root.value ?? { id: '', kind: 'course', label: '', status: null, facts: {}, children: [], deps: [] }))

const query = ref('')
const heldOnly = ref(false)
const open = ref<Set<string>>(new Set())
const shown = computed(() => (root.value ? filterTree(root.value, index.value, query.value, heldOnly.value) : null))
const opened = ref(false)
watch(root, (next) => {
  if (next && !opened.value) {
    opened.value = true
    open.value = new Set(next.children.map((run) => run.id))
  }
}, { immediate: true })
watch(shown, (next) => {
  if (next) open.value = new Set([...open.value, ...next.trail])
})

function setOpen(nodeId: string, value: boolean): void {
  const next = new Set(open.value)
  if (value) next.add(nodeId)
  else next.delete(nodeId)
  open.value = next
}
async function goTo(nodeId: string): Promise<void> {
  query.value = ''
  heldOnly.value = false
  open.value = new Set([...open.value, ...pathTo(index.value, nodeId).slice(0, -1)])
  await nextTick()
  const el = document.querySelector<HTMLElement>(`[data-node="${nodeId}"]`)
  const target = el?.querySelector<HTMLElement>('summary') ?? el
  target?.scrollIntoView({ block: 'center' })
  target?.focus()
}
const callOpen = ref(false)
const callRun = ref<string | null>(null)
const callId = ref<string | null>(null)
function showCall(node: CallTreeNode): void {
  callRun.value = String(node.facts.run_id)
  callId.value = String(node.facts.call_id)
  callOpen.value = true
}
provide(CHAIN_CONTEXT, { courseId: id, index, open, shown, setOpen, goTo, showCall })

const empty = computed(() => root.value !== null && Number(root.value.facts.calls) === 0 && root.value.children.length === 0)
const live = computed(() => root.value?.facts.live === true)
const tiles = computed(() => [
  { id: 'calls', label: t('calls.tileCalls'), value: String(root.value?.facts.calls ?? 0) },
  { id: 'cost', label: t('calls.tileCost'), value: usdFine(String(root.value?.facts.cost_usd ?? '0')) ?? '' },
  { id: 'accepted', label: t('calls.tileAccepted'), value: String(root.value?.facts.blocks_accepted ?? 0) },
  { id: 'held', label: t('calls.tileHeld'), value: String(root.value?.facts.blocks_held ?? 0) },
])
const topLevel = computed(() => (root.value?.children ?? []).filter((run) => !shown.value || shown.value.visible.has(run.id)))
</script>

<template>
  <PageHeader :course="course.data.value" />
  <ResourceState :loading="tree.loading.value" :error="tree.error.value" :empty="empty" :empty-text="t('chains.empty')" @retry="tree.reload()">
    <template #empty-actions>
      <router-link :to="`/courses/${id}/plan`" data-testid="chains-go-plan">{{ t('generate.goPlan') }}</router-link>
    </template>
    <div class="stack" data-testid="chains">
      <StatTiles :tiles="tiles" data-testid="chains-tiles" />
      <BuildFlow v-if="root" :root="root" />
      <div v-if="live" class="row" role="status" data-testid="chains-live">
        <span>{{ t('chains.live') }}</span>
        <ActionButton action="refresh" data-testid="chains-refresh" @click="tree.reload(true)" />
      </div>
      <div class="row">
        <SearchField v-model="query" :placeholder="t('chains.searchPlaceholder')" data-testid="chains-search" />
        <label class="row"><input v-model="heldOnly" type="checkbox" data-testid="chains-held-only"> {{ t('chains.heldOnly') }}</label>
      </div>
      <p v-if="shown && shown.matches.size === 0" class="muted" data-testid="chains-no-match">{{ t('chains.noMatch') }}</p>
      <ul class="plain-list chain-tree" data-testid="chains-tree">
        <ChainNode v-for="run in topLevel" :key="run.id" :node="run" />
      </ul>
    </div>
  </ResourceState>
  <CallDetailDrawer v-model:visible="callOpen" :run-id="callRun" :call-id="callId" />
</template>
