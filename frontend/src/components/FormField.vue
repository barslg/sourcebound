<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { TriangleAlert } from 'lucide-vue-next'

const props = defineProps<{ id: string; label: string; help: string; required?: boolean; error?: string | null; group?: boolean }>()
const { t } = useI18n()
const touched = ref(false)
const shown = computed(() => touched.value && Boolean(props.error))
</script>

<template>
  <component :is="group ? 'fieldset' : 'div'" class="field" :class="{ 'field-invalid': shown }" @focusout="touched = true">
    <legend v-if="group" class="field-label">{{ label }}<span v-if="required" class="field-required">({{ t('form.required') }})</span></legend>
    <div v-else class="field-label">
      <label :for="id">{{ label }}</label><span v-if="required" class="field-required">({{ t('form.required') }})</span>
    </div>
    <slot :invalid="shown" :described-by="`${id}-help`" />
    <span :id="`${id}-help`" class="hint">{{ help }}</span>
    <span v-if="shown" class="field-error" role="alert" data-testid="field-error"><TriangleAlert :size="14" :stroke-width="2" aria-hidden="true" />{{ error }}</span>
  </component>
</template>
