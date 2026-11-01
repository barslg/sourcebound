<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ActionButton from '@/components/ActionButton.vue'

const props = defineProps<{ id: string; file: File | null }>()
const emit = defineEmits<{ pick: [file: File | null] }>()
const { t } = useI18n()
const input = ref<HTMLInputElement | null>(null)

function onChange(event: Event): void {
  emit('pick', (event.target as HTMLInputElement).files?.[0] ?? null)
}
watch(() => props.file, (now) => { if (now === null && input.value) input.value.value = '' })
function clear(): void {
  if (input.value) input.value.value = ''
  emit('pick', null)
}
defineExpose({ clear })
</script>

<template>
  <div class="file-picker row">
    <input :id="id" ref="input" class="file-native" type="file" tabindex="-1" data-testid="source-file" @change="onChange" />
    <ActionButton action="choose-file" :label="t('sources.fileChoose')" data-testid="file-choose" @click="input?.click()" />
    <template v-if="file">
      <span class="mono file-name" data-testid="file-name">{{ file.name }}</span>
      <ActionButton action="remove-file" :label="t('sources.fileRemove')" size="small" data-testid="file-remove" @click="clear" />
    </template>
  </div>
</template>
