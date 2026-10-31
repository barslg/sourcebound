<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import InputText from 'primevue/inputtext'
import { ACTION_ICONS } from '@/ui/actionIcons'

defineOptions({ inheritAttrs: false })
defineProps<{ modelValue: string; placeholder: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const { t } = useI18n()
const Icon = ACTION_ICONS.search
</script>

<template>
  <div class="search-field">
    <Icon :size="16" :stroke-width="2" aria-hidden="true" />
    <InputText :model-value="modelValue" type="search" :placeholder="placeholder" :aria-label="t('action.search')" v-bind="$attrs" data-search @update:model-value="emit('update:modelValue', $event ?? '')" />
  </div>
</template>
