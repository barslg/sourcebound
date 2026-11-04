<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Checkbox from 'primevue/checkbox'
import InputText from 'primevue/inputtext'
import ActionButton from '@/components/ActionButton.vue'
import FormField from '@/components/FormField.vue'
import Queued from '@/components/Queued.vue'
import { COURSE_LANGUAGES, METHODOLOGY_CODES } from '@/composables/methodologies'
import { firstReason, lacking } from '@/composables/reasons'

export interface Brief { levels: string; context: string; mustCover: string; outcomes: string; languages: string[] }

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
  opQueued: boolean
  noKey: string | null
  buildingWhole: boolean
}>()
const methodologies = defineModel<string[]>('methodologies', { required: true })
const brief = defineModel<Brief>('brief', { required: true })
const cap = defineModel<string>('cap', { required: true })
const emit = defineEmits<{ submit: []; build: [] }>()

const { t } = useI18n()
/** An empty cap means no limit; a typed one must be above zero. */
const capBad = computed(() => cap.value.trim() !== '' && !(Number(cap.value) > 0))
const briefMissing = computed(() => !brief.value.levels.trim() || !brief.value.context.trim() || !brief.value.outcomes.trim())
const commonReasons = computed(() => [lacking('course.plan'), methodologies.value.length === 0 && t('plan.pickMethodology'), briefMissing.value && t('plan.briefRequired'), props.opQueued && t('plan.alreadyQueued')])
</script>

<template>
  <form :id="props.formId" class="stack build-form" :data-testid="props.cardTestid" @submit.prevent="emit('submit')">
    <FormField :id="`${props.fieldPrefix}-meth`" group :label="t('plan.methodology')" :help="t('plan.methodologyHint')" required :error="methodologies.length === 0 ? t('plan.pickMethodology') : null">
      <div v-for="code in METHODOLOGY_CODES" :key="code" class="perm-check">
        <Checkbox v-model="methodologies" :input-id="`${props.choicePrefix}-${code}`" :value="code" />
        <label :for="`${props.choicePrefix}-${code}`"><strong>{{ t(`methodology.${code}.name`) }}</strong> <span class="hint">{{ t(`methodology.${code}.line`) }}</span></label>
      </div>
    </FormField>
    <FormField :id="`${props.fieldPrefix}-levels`" :label="t('plan.briefLevels')" :help="t('plan.briefLevelsHint')" required :error="brief.levels.trim() ? null : t('plan.briefRequired')"><textarea :id="`${props.fieldPrefix}-levels`" v-model="brief.levels" rows="2"></textarea></FormField>
    <FormField :id="`${props.fieldPrefix}-context`" :label="t('plan.briefContext')" :help="t('plan.briefContextHint')" required :error="brief.context.trim() ? null : t('plan.briefRequired')"><textarea :id="`${props.fieldPrefix}-context`" v-model="brief.context" rows="3"></textarea></FormField>
    <FormField :id="`${props.fieldPrefix}-must`" :label="t('plan.briefMustCover')" :help="t('plan.briefMustCoverHint')"><textarea :id="`${props.fieldPrefix}-must`" v-model="brief.mustCover" rows="3"></textarea></FormField>
    <FormField :id="`${props.fieldPrefix}-outcomes`" :label="t('plan.briefOutcomes')" :help="t('plan.briefOutcomesHint')" required :error="brief.outcomes.trim() ? null : t('plan.briefRequired')"><textarea :id="`${props.fieldPrefix}-outcomes`" v-model="brief.outcomes" rows="3"></textarea></FormField>
    <FormField :id="`${props.fieldPrefix}-langs`" group :label="t('plan.briefLanguages')" :help="t('plan.briefLanguagesHint')">
      <div v-for="code in COURSE_LANGUAGES" :key="code" class="perm-check">
        <Checkbox v-model="brief.languages" :input-id="`${props.choicePrefix}-lang-${code}`" :value="code" />
        <label :for="`${props.choicePrefix}-lang-${code}`">{{ t(`language.${code}`) }}</label>
      </div>
    </FormField>
    <FormField :id="`${props.fieldPrefix}-cap`" :label="t('build.capLabel')" :help="t('build.capOptionalHint')" :error="capBad ? t('build.capInvalid') : null"><InputText :id="`${props.fieldPrefix}-cap`" v-model="cap" inputmode="decimal" :data-testid="`${props.fieldPrefix}-cap`" /></FormField>
    <p v-if="props.buildError" class="mono danger-text" role="alert" data-testid="build-error">{{ props.buildError }}</p>
    <div class="row">
      <ActionButton type="submit" action="build-plan" :label="props.submitLabel" :loading="props.building" :reason="firstReason(...commonReasons)" :data-testid="props.submitTestid" />
      <ActionButton action="build-course" :loading="props.buildingWhole" :reason="firstReason(props.noKey, lacking('course.generate'), ...commonReasons, capBad && t('build.capInvalid'))" :data-testid="`${props.submitTestid}-whole`" @click="emit('build')" />
    </div>
    <Queued v-if="props.opQueued" />
  </form>
</template>
