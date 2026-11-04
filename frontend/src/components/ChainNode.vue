<script setup lang="ts">
import { computed, inject } from 'vue'
import { useI18n } from 'vue-i18n'
import type { CallTreeNode } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import RelTime from '@/components/RelTime.vue'
import StatusChip from '@/components/StatusChip.vue'
import { useCallLabels } from '@/composables/callLabels'
import { CHAIN_CONTEXT, type ChainContext } from '@/composables/chainContext'
import { holdText } from '@/composables/holdReason'
import { usdFine } from '@/composables/labels'

const props = defineProps<{ node: CallTreeNode }>()
const { t, te } = useI18n()
const injected = inject(CHAIN_CONTEXT)
if (!injected) throw new Error('ChainNode needs the chains page around it')
const ctx: ChainContext = injected
const { agentName, statusLabel, statusTone, tokens } = useCallLabels()

const fact = (key: string): string => String(props.node.facts[key] ?? '')
const isOpen = computed(() => ctx.open.value.has(props.node.id))
const children = computed(() => (ctx.shown.value ? props.node.children.filter((c) => ctx.shown.value?.visible.has(c.id)) : props.node.children))
const isCall = computed(() => props.node.kind === 'call')
const word = (key: string, fallback: string): string => (te(key) ? t(key) : fallback)

const label = computed(() => {
  const n = props.node
  if (n.kind === 'run') return word(`runs.kind.${fact('run_kind')}`, fact('run_kind'))
  if (n.kind === 'group') {
    const group = fact('group')
    return group === 'research' && fact('query') ? t('chains.groupQuery', { query: fact('query') }) : word(`chains.group.${group}`, group)
  }
  if (n.kind === 'block') return `${word(`learn.type.${fact('block_type')}`, fact('block_type'))} (${word(`chains.role.${fact('role')}`, fact('role'))})`
  if (n.kind === 'call') return agentName(fact('agent'))
  return n.label
})
const statusText = computed(() => {
  const n = props.node
  if (n.kind === 'run') return word(`run.status.${n.status}`, String(n.status))
  return word(`chains.block.${n.status}`, String(n.status))
})
const tone = computed<'ok' | 'warn' | 'danger' | 'info'>(() => {
  const s = props.node.status
  return s === 'done' || s === 'accepted' ? 'ok' : s === 'held' ? 'warn' : s === 'stopped' ? 'danger' : 'info'
})
const call = computed(() => ({ status: fact('status') || String(props.node.status), cached_reply: props.node.facts.cached_reply === true }))
const totals = computed(() => t('chains.totals', { calls: fact('calls'), cost: usdFine(fact('cost_usd')) ?? '' }))
const callLine = computed(() => t('chains.callLine', {
  model: fact('display_name'), cost: usdFine(fact('cost_usd')) ?? '', tokens: tokens(props.node.facts as never), seconds: (Number(props.node.facts.duration_ms) / 1000).toFixed(1),
}))
const holds = computed(() => ((props.node.facts.hold_lines as string[] | undefined) ?? []).map((line) => holdText(line, (id) => t(`holdItem.${id}`))))
const deps = computed(() => props.node.deps.map((id) => ({ id, node: ctx.index.value.byId.get(id) })).filter((d) => d.node !== undefined))
const depName = (node: CallTreeNode): string => (node.kind === 'call' ? t('chains.depCall', { step: agentName(String(node.facts.agent)), when: new Date(String(node.facts.created_at)).toLocaleString() }) : t('chains.depBlock', { id: String(node.facts.plan_node_id) }))
function onToggle(event: Event): void {
  const open = (event.target as HTMLDetailsElement).open
  if (open !== isOpen.value) ctx.setOpen(props.node.id, open)
}
</script>

<template>
  <li v-if="isCall" class="chain-call" :data-node="node.id" tabindex="-1" data-testid="chain-call">
    <span class="chain-label">{{ label }}</span>
    <StatusChip :tone="statusTone(call)" :label="statusLabel(call)" />
    <span class="hint">{{ callLine }} <RelTime :value="fact('created_at')" /></span>
    <ActionButton action="details" size="small" :label="t('calls.show')" data-testid="chain-call-open" @click="ctx.showCall(node)" />
  </li>
  <li v-else :data-node="node.id" :data-kind="node.kind">
    <details class="chain-node" :open="isOpen" @toggle="onToggle">
      <summary class="chain-head" :data-testid="`chain-${node.kind}`">
        <span class="chain-label">{{ label }}</span>
        <span v-if="node.kind === 'block'" class="mono chain-id">{{ fact('plan_node_id') }}</span>
        <span v-else-if="node.kind === 'topic'" class="mono chain-id">{{ fact('plan_node_id') }}</span>
        <span v-else-if="node.kind === 'group' && fact('group') === 'other'" class="mono chain-id">{{ fact('step_key') }}</span>
        <StatusChip v-if="node.status" :tone="tone" :label="statusText" />
        <span class="hint">{{ totals }}</span>
      </summary>
      <div v-if="isOpen" class="chain-body stack">
        <p v-if="node.kind === 'run' && node.facts.stop_marker" class="hint">{{ node.facts.stop_marker }}</p>
        <div v-if="holds.length > 0" class="stack" data-testid="chain-holds">
          <p class="hint">{{ t('chains.heldBecause') }}</p>
          <ul class="plain-list"><li v-for="line in holds" :key="line">{{ line }}</li></ul>
          <router-link :to="`/courses/${ctx.courseId.value}/fix#held-${fact('block_id')}`" data-testid="chain-fix">{{ t('chains.openFix') }}</router-link>
        </div>
        <div v-if="deps.length > 0" data-testid="chain-deps">
          <p class="hint">{{ t(node.kind === 'block' ? 'chains.dependsOn' : 'chains.readsBlocks') }}</p>
          <ul class="plain-list">
            <li v-for="d in deps" :key="d.id"><a :href="`#${d.id}`" data-testid="chain-dep" @click.prevent="ctx.goTo(d.id)">{{ depName(d.node as CallTreeNode) }}</a></li>
          </ul>
        </div>
        <ul v-if="children.length > 0" class="plain-list chain-children">
          <ChainNode v-for="child in children" :key="child.id" :node="child" />
        </ul>
      </div>
    </details>
  </li>
</template>
