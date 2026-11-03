<script setup lang="ts">
import ActionButton from '@/components/ActionButton.vue'

/** A block of detail folded behind one chevron button in the corner; the label says what unfolds. */
defineProps<{ id: string; label: string; open: boolean }>()
defineEmits<{ 'update:open': [value: boolean] }>()
</script>

<template>
  <div class="fold" data-testid="fold">
    <div class="fold-head">
      <ActionButton action="details" size="small" :label="label" :aria-expanded="open" :aria-controls="id" :data-testid="`disclosure-${id}`" @click="$emit('update:open', !open)" />
    </div>
    <div v-if="open" :id="id" class="fold-body"><slot /></div>
  </div>
</template>
