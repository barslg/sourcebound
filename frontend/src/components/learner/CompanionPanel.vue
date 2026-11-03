<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError, request } from '@/api/client'
import { learnPassage } from '@/api/endpoints'
import type { LearnPassage } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import ResourceState from '@/components/ResourceState.vue'

const props = defineProps<{ passageId: string | null; language: string; passagePath?: (id: string) => string }>()
const emit = defineEmits<{ close: [] }>()
const { t } = useI18n()
const passage = ref<LearnPassage | null>(null)
const error = ref<ApiError | null>(null)
const loading = ref(false)
let ticket = 0

async function load(): Promise<void> {
  const id = props.passageId
  if (!id) { passage.value = null; error.value = null; return }
  const mine = ++ticket
  loading.value = true
  try {
    const next = await request<LearnPassage>((props.passagePath ?? learnPassage)(id), { query: { lang: props.language } })
    if (mine === ticket) { passage.value = next; error.value = null }
  } catch (cause) {
    if (mine === ticket) error.value = cause instanceof ApiError ? cause : new ApiError(0, String(cause))
  } finally {
    if (mine === ticket) loading.value = false
  }
}
watch(() => [props.passageId, props.language], () => { void load() }, { immediate: true })
</script>

<template>
  <aside id="companion-panel" class="companion stack" :aria-label="t('learn.companion.title')" data-testid="companion">
    <h2>{{ t('learn.companion.title') }}</h2>
    <p v-if="!passageId" class="muted" data-testid="companion-empty">{{ t('learn.companion.hint') }}</p>
    <ResourceState v-else :loading="loading" :error="error" :empty="false" @retry="load">
      <div v-if="passage" class="stack" data-testid="passage">
        <p class="muted"><strong>{{ passage.source_title }}</strong> · {{ passage.address_label }}<template v-if="passage.as_of"> · {{ t('learn.companion.asOf', { date: passage.as_of }) }}</template></p>
        <blockquote class="passage-text" data-testid="passage-text">{{ passage.text }}</blockquote>
      </div>
    </ResourceState>
    <ActionButton v-if="passageId" action="cancel" :label="t('learn.companion.close')" size="small" data-testid="companion-close" @click="emit('close')" />
    <slot />
  </aside>
</template>
