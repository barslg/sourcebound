<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import Checkbox from 'primevue/checkbox'
import ActionButton from '@/components/ActionButton.vue'
import FormField from '@/components/FormField.vue'
import Queued from '@/components/Queued.vue'
import { METHODOLOGY_CODES } from '@/composables/methodologies'
import { firstReason, lacking } from '@/composables/reasons'

const props = defineProps<{
  courseId: string
  formId: string
  fieldPrefix: string
  choicePrefix: string
  cardTestid: string
  submitTestid: string
  submitLabel: string
  building: boolean
  buildError: string | null
  noSources: boolean
  opQueued: boolean
}>()
const methodologies = defineModel<string[]>('methodologies', { required: true })
const queriesText = defineModel<string>('queriesText', { required: true })
const emit = defineEmits<{ submit: [] }>()

const { t } = useI18n()
</script>

<template>
  <form :id="props.formId" class="stack build-form" :data-testid="props.cardTestid" @submit.prevent="emit('submit')">
    <FormField :id="`${props.fieldPrefix}-meth`" group :label="t('plan.methodology')" :help="t('plan.methodologyHint')" required :error="methodologies.length === 0 ? t('plan.pickMethodology') : null">
      <div v-for="code in METHODOLOGY_CODES" :key="code" class="perm-check">
        <Checkbox v-model="methodologies" :input-id="`${props.choicePrefix}-${code}`" :value="code" />
        <label :for="`${props.choicePrefix}-${code}`"><strong>{{ t(`methodology.${code}.name`) }}</strong> <span class="hint">{{ t(`methodology.${code}.line`) }}</span></label>
      </div>
    </FormField>
    <FormField :id="`${props.fieldPrefix}-q`" :label="t('plan.queries')" :help="t('plan.queriesHint')" required :error="queriesText.trim().length === 0 ? t('plan.typeQueries') : null"><textarea :id="`${props.fieldPrefix}-q`" v-model="queriesText" rows="3"></textarea></FormField>
    <router-link v-if="props.noSources" :to="`/courses/${props.courseId}/sources`" data-testid="go-sources">{{ t('plan.goSources') }}</router-link>
    <p v-if="props.buildError" class="mono danger-text" role="alert" data-testid="build-error">{{ props.buildError }}</p>
    <div>
      <ActionButton type="submit" action="build-plan" :label="props.submitLabel" :loading="props.building" :reason="firstReason(lacking('course.plan'), props.noSources && t('plan.addSourceFirst'), methodologies.length === 0 && t('plan.pickMethodology'), queriesText.trim().length === 0 && t('plan.typeQueries'), props.opQueued && t('plan.alreadyQueued'))" :data-testid="props.submitTestid" />
    </div>
    <Queued v-if="props.opQueued" />
  </form>
</template>
