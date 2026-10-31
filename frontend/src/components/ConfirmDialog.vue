<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ActionButton from '@/components/ActionButton.vue'
import AppDialog from '@/components/AppDialog.vue'
import FormField from '@/components/FormField.vue'
import type { ActionId } from '@/ui/actionMeta'
import InputText from 'primevue/inputtext'

const props = defineProps<{
  visible: boolean
  title: string
  body?: string
  action: ActionId
  label?: string
  busy?: boolean
  error?: string | null
  typed?: string | null
  blockedReason?: string | null
  solid?: boolean
}>()
const emit = defineEmits<{ 'update:visible': [value: boolean]; confirm: [] }>()
const { t } = useI18n()
const typedText = ref('')
watch(() => props.visible, (open) => { if (open) typedText.value = '' })
const reason = computed(() => {
  if (props.blockedReason) return props.blockedReason
  if (props.typed && typedText.value.trim() !== props.typed) return t('confirm.typeFirst', { name: props.typed })
  return null
})
</script>

<template>
  <AppDialog :visible="visible" :header="title" size="sm" @update:visible="emit('update:visible', $event)">
    <form class="stack" @submit.prevent="!reason && emit('confirm')">
      <p v-if="body">{{ body }}</p>
      <slot />
      <FormField v-if="typed" id="confirm-typed" :label="t('confirm.typeLabel', { name: typed })" :help="t('confirm.typeHint')">
        <InputText id="confirm-typed" v-model="typedText" autocomplete="off" />
      </FormField>
      <p v-if="error" class="mono danger-text" role="alert" data-testid="form-error">{{ error }}</p>
      <div class="row">
        <ActionButton action="cancel" @click="emit('update:visible', false)" />
        <ActionButton type="submit" class="row-end" :action="action" :label="label" :solid="solid ?? true" :loading="busy" :reason="reason" data-testid="dialog-submit" />
      </div>
    </form>
  </AppDialog>
</template>
