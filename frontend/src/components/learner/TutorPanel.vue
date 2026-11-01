<script setup lang="ts">
import InputText from 'primevue/inputtext'
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChevronDown, ChevronRight } from 'lucide-vue-next'
import { ApiError, request } from '@/api/client'
import { learnAsk } from '@/api/endpoints'
import type { AskReply, Chip } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import FormField from '@/components/FormField.vue'
import { lacking } from '@/composables/reasons'
import CitationChip from './CitationChip.vue'

const props = defineProps<{ courseId: string; language: string; openId?: string | null }>()
const emit = defineEmits<{ open: [chip: Chip] }>()
const { t } = useI18n()
const expanded = ref(false)
const question = ref('')
const reply = ref<AskReply | null>(null)
const error = ref<ApiError | null>(null)
const busy = ref(false)
const denied = lacking('learn.ask')

async function ask(): Promise<void> {
  const text = question.value.trim()
  if (text === '') return
  busy.value = true
  error.value = null
  try {
    reply.value = await request<AskReply>(learnAsk(props.courseId), { method: 'POST', body: { question: text }, query: { lang: props.language } })
  } catch (cause) {
    reply.value = null
    error.value = cause instanceof ApiError ? cause : new ApiError(0, String(cause))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="tutor stack" data-testid="tutor">
    <h2 class="tutor-head">
      <button type="button" class="disclosure" :aria-expanded="expanded" aria-controls="tutor-body" data-testid="tutor-toggle" @click="expanded = !expanded">
        <component :is="expanded ? ChevronDown : ChevronRight" :size="16" :stroke-width="2" aria-hidden="true" />{{ t('learn.tutor.title') }}
      </button>
    </h2>
    <div v-show="expanded" id="tutor-body" class="stack" data-testid="tutor-body">
      <p class="hint">{{ t('learn.tutor.help') }}</p>
      <FormField id="tutor-q" :label="t('learn.tutor.question')" :help="t('learn.tutor.questionHelp')">
        <InputText id="tutor-q" v-model="question" maxlength="2000" data-testid="tutor-question" @keydown.enter.prevent="ask" />
      </FormField>
      <div><ActionButton action="ask" :label="t('learn.tutor.ask')" :loading="busy" :reason="denied ?? (question.trim() === '' ? t('learn.tutor.needQuestion') : null)" data-testid="tutor-ask" @click="ask" /></div>
      <div v-if="error" class="state state-error" role="alert" data-testid="tutor-error">
        <p class="mono">{{ error.message }}</p>
        <ActionButton action="retry" @click="ask" />
      </div>
      <div v-else-if="reply?.mode === 'not_in_materials'" class="state" role="status" data-testid="tutor-none">
        <p>{{ t('learn.tutor.notInMaterials') }}</p>
        <p class="muted">{{ reply.hint }}</p>
      </div>
      <ul v-else-if="reply?.mode === 'quotes'" class="plain-list" data-testid="tutor-quotes">
        <li v-for="(q, i) in reply.quotes" :key="i" class="bell-item stack" data-testid="tutor-quote">
          <blockquote class="passage-text">{{ q.quote }}</blockquote>
          <CitationChip :chip="q.chip" :open="openId === q.chip.passage_id" @open="emit('open', $event)" />
        </li>
      </ul>
    </div>
  </section>
</template>
