<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { CallTreeNode } from '@/api/types'
import { usdFine } from '@/composables/labels'

const props = defineProps<{ root: CallTreeNode }>()
const { t } = useI18n()

interface Stage { id: string; agents: string[]; code?: boolean }
/** The lecture-first chain in order: the brief, then one stage per agent, the block plan by code. */
const STAGES: Stage[] = [
  { id: 'brief', agents: [], code: true },
  { id: 'blueprint', agents: ['blueprint', 'outline', 'architect'] },
  { id: 'lectures', agents: ['lecture'] },
  { id: 'blockplan', agents: [], code: true },
  { id: 'illustrations', agents: ['illustrator'] },
  { id: 'exercises', agents: ['block_exercise', 'block_teach', 'block_example'] },
  { id: 'corrector', agents: ['corrector', 'reader'] },
  { id: 'translation', agents: ['translator'] },
]

interface Tally { calls: number; cost: number }
function tally(node: CallTreeNode, into: Record<string, Tally>): void {
  if (node.kind === 'call') {
    const agent = String(node.facts.agent)
    const row = (into[agent] ??= { calls: 0, cost: 0 })
    row.calls += 1
    row.cost += Number(node.facts.cost_usd ?? 0)
  }
  for (const child of node.children) tally(child, into)
}
const byAgent = computed(() => {
  const into: Record<string, Tally> = {}
  tally(props.root, into)
  return into
})
const stages = computed(() =>
  STAGES.map((stage) => {
    const rows = stage.agents.map((a) => byAgent.value[a]).filter((r): r is Tally => r !== undefined)
    const calls = rows.reduce((n, r) => n + r.calls, 0)
    const cost = rows.reduce((n, r) => n + r.cost, 0)
    return { ...stage, calls, cost, shown: stage.code || calls > 0 }
  }).filter((s) => s.shown),
)
</script>

<template>
  <section class="build-flow" aria-labelledby="build-flow-title" data-testid="build-flow">
    <h2 id="build-flow-title">{{ t('chains.flow.title') }}</h2>
    <p class="hint">{{ t('chains.flow.help') }}</p>
    <ol class="flow-list">
      <li v-for="stage in stages" :key="stage.id" class="flow-stage" :data-stage="stage.id">
        <strong>{{ t(`chains.flow.stage.${stage.id}`) }}</strong>
        <span class="hint">{{ t(`chains.flow.line.${stage.id}`) }}</span>
        <span v-if="stage.code" class="muted">{{ t('chains.flow.byCode') }}</span>
        <span v-else class="muted">{{ t('chains.flow.calls', { calls: stage.calls, cost: usdFine(stage.cost.toFixed(4)) ?? '' }) }}</span>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.flow-list { display: flex; flex-wrap: wrap; gap: 0.75rem; padding: 0; margin: 0.5rem 0 0; list-style: none; counter-reset: stage; }
.flow-stage { display: flex; flex-direction: column; gap: 0.15rem; flex: 1 1 11rem; min-width: 11rem; padding: 0.6rem 0.75rem; border: 1px solid var(--line, #ccc); border-radius: 0.5rem; counter-increment: stage; }
.flow-stage::before { content: counter(stage); font-size: 0.75rem; opacity: 0.7; }
</style>
