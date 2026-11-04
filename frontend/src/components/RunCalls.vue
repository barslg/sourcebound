<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Column from 'primevue/column'
import { request } from '@/api/client'
import { runCall, runCalls } from '@/api/endpoints'
import type { CallRow, CallSummaryRow, RunCallDetail, RunCallsDoc } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import AppDrawer from '@/components/AppDrawer.vue'
import AppTable from '@/components/AppTable.vue'
import RelTime from '@/components/RelTime.vue'
import ResourceState from '@/components/ResourceState.vue'
import Section from '@/components/Section.vue'
import StatusChip from '@/components/StatusChip.vue'
import { usdFine } from '@/composables/labels'
import { notifyError } from '@/composables/notify'
import { useResource } from '@/composables/useResource'

const props = defineProps<{ runId: string; live: boolean; compact?: boolean }>()
const { t, te } = useI18n()
const calls = useResource<RunCallsDoc>(`calls:${props.runId}`, (signal) => request<RunCallsDoc>(`${runCalls(props.runId)}?limit=200`, { signal }))
const doc = computed(() => calls.data.value)
let timer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  void calls.reload()
  timer = setInterval(() => { if (props.live) void calls.reload(true) }, 3000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

const totalCost = computed(() => (doc.value?.summary ?? []).reduce((sum, row) => sum + Number(row.cost_usd), 0))
const unknown = computed(() => t('calls.unknown'))
const count = (value: number | null | undefined): string => (value === null || value === undefined ? unknown.value : String(value))
const quotes = computed(() => {
  const n = doc.value?.numbers
  return n && n.quotes_verbatim !== null && n.quotes_total !== null ? t('calls.quotesOf', { ok: n.quotes_verbatim, all: n.quotes_total }) : unknown.value
})
const tiles = computed(() => [
  { id: 'calls', label: t('calls.tileCalls'), value: String(doc.value?.total ?? 0) },
  { id: 'cost', label: t('calls.tileCost'), value: usdFine(totalCost.value) ?? unknown.value },
  { id: 'accepted', label: t('calls.tileAccepted'), value: count(doc.value?.numbers.blocks_accepted) },
  { id: 'held', label: t('calls.tileHeld'), value: count(doc.value?.numbers.blocks_held) },
  { id: 'quotes', label: t('calls.tileQuotes'), value: quotes.value },
])

const agentName = (agent: string): string => (te(`run.role.${agent}`) ? t(`run.role.${agent}`) : te(`calls.agent.${agent}`) ? t(`calls.agent.${agent}`) : agent)
const tokens = (row: { tokens_input: number; tokens_cached: number; tokens_output: number; tokens_reasoning: number }): string =>
  t('calls.tokensLine', { input: row.tokens_input, cached: row.tokens_cached, output: row.tokens_output + row.tokens_reasoning })
const statusTone = (row: CallRow): 'ok' | 'info' | 'warn' => (row.status === 'completed' ? 'ok' : row.status === 'pending' ? 'info' : 'warn')
const statusLabel = (row: CallRow): string => (row.cached_reply ? t('calls.status.replayed') : te(`calls.status.${row.status}`) ? t(`calls.status.${row.status}`) : row.status)
const shortId = (id: string | null): string | null => (id ? (id.length > 16 ? `${id.slice(0, 14)}...` : id) : null)

const open = ref(false)
const shown = ref<RunCallDetail | null>(null)
const opening = ref<string | null>(null)
async function show(row: CallRow): Promise<void> {
  opening.value = row.id
  try {
    shown.value = await request<RunCallDetail>(runCall(props.runId, row.id))
    open.value = true
  } catch (cause) {
    notifyError(cause)
  } finally {
    opening.value = null
  }
}
const pretty = (value: unknown): string => (value === null || value === undefined ? t('calls.noReply') : JSON.stringify(value, null, 2))
</script>

<template>
  <section class="stack" data-testid="run-calls">
    <Section :title="t('calls.title')" :help="compact ? t('calls.helpCompact') : t('calls.help')" />
    <ResourceState :loading="calls.loading.value" :error="calls.error.value" :empty="(doc?.total ?? 0) === 0 && doc?.numbers.blocks_accepted == null" :empty-text="t('calls.empty')" @retry="calls.reload()">
      <ul class="stat-tiles" data-testid="calls-tiles">
        <li v-for="tile in tiles" :key="tile.id" class="stat-tile" :data-tile="tile.id">
          <span class="stat-value">{{ tile.value }}</span>
          <span class="stat-label">{{ tile.label }}</span>
        </li>
      </ul>
      <p v-if="(doc?.total ?? 0) === 0" class="muted" data-testid="calls-none">{{ t('calls.none') }}</p>
      <template v-else>
        <AppTable :value="doc?.summary ?? []" data-key="agent" data-testid="calls-summary">
          <Column field="agent" :header="t('calls.agentHeader')"><template #body="{ data }">{{ agentName((data as CallSummaryRow).agent) }}</template></Column>
          <Column field="model" :header="t('calls.model')"><template #body="{ data }">{{ (data as CallSummaryRow).display_name }} <span class="mono muted">{{ (data as CallSummaryRow).model }}</span></template></Column>
          <Column field="calls" :header="t('calls.count')" />
          <Column :header="t('calls.tokens')"><template #body="{ data }">{{ tokens(data as CallSummaryRow) }}</template></Column>
          <Column field="cost_usd" :header="t('calls.cost')"><template #body="{ data }">{{ usdFine((data as CallSummaryRow).cost_usd) }}</template></Column>
        </AppTable>
        <router-link v-if="compact" :to="`/runs/${runId}`" data-testid="calls-open-run">{{ t('calls.openRun') }}</router-link>
        <template v-else>
          <p v-if="(doc?.calls.length ?? 0) < (doc?.total ?? 0)" class="hint" data-testid="calls-capped">{{ t('calls.capped', { shown: doc?.calls.length ?? 0, total: doc?.total ?? 0 }) }}</p>
          <AppTable :value="doc?.calls ?? []" data-key="id" data-testid="calls-list">
            <Column field="created_at" :header="t('calls.time')"><template #body="{ data }"><RelTime :value="(data as CallRow).created_at" /></template></Column>
            <Column field="agent" :header="t('calls.agentHeader')"><template #body="{ data }">{{ agentName((data as CallRow).agent) }}</template></Column>
            <Column field="model" :header="t('calls.model')"><template #body="{ data }">{{ (data as CallRow).display_name }}</template></Column>
            <Column field="status" :header="t('calls.statusLabel')"><template #body="{ data }"><StatusChip :tone="statusTone(data as CallRow)" :label="statusLabel(data as CallRow)" /></template></Column>
            <Column :header="t('calls.tokens')"><template #body="{ data }">{{ tokens(data as CallRow) }}</template></Column>
            <Column field="cost_usd" :header="t('calls.cost')"><template #body="{ data }">{{ usdFine((data as CallRow).cost_usd) }}</template></Column>
            <Column field="duration_ms" :header="t('calls.duration')"><template #body="{ data }">{{ t('calls.ms', { ms: (data as CallRow).duration_ms }) }}</template></Column>
            <Column field="response_id" :header="t('calls.responseId')"><template #body="{ data }"><span class="mono" :title="(data as CallRow).response_id ?? ''">{{ shortId((data as CallRow).response_id) ?? unknown }}</span></template></Column>
            <Column :header="t('calls.inspect')">
              <template #body="{ data }">
                <ActionButton action="details" size="small" :label="t('calls.show')" :loading="opening === (data as CallRow).id" data-testid="call-open" @click="show(data as CallRow)" />
              </template>
            </Column>
          </AppTable>
        </template>
      </template>
    </ResourceState>
    <AppDrawer v-model:visible="open" :header="t('calls.drawerTitle', { agent: agentName(shown?.agent ?? ''), model: shown?.display_name ?? '' })">
      <div v-if="shown" class="stack" data-testid="call-detail">
        <p class="hint">{{ t('calls.drawerHelp', { model: shown.model }) }}</p>
        <h3>{{ t('calls.request') }}</h3>
        <pre class="call-json mono" tabindex="0" data-testid="call-request">{{ pretty(shown.request) }}</pre>
        <h3>{{ t('calls.response') }}</h3>
        <pre class="call-json mono" tabindex="0" data-testid="call-response">{{ pretty(shown.response) }}</pre>
      </div>
    </AppDrawer>
  </section>
</template>
