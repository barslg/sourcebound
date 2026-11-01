<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import AppDialog from '@/components/AppDialog.vue'
import { ACTION_ICONS } from '@/ui/actionIcons'
import { ACTIONS } from '@/ui/actionMeta'

defineProps<{ visible: boolean }>()
const emit = defineEmits<{ 'update:visible': [value: boolean] }>()
const { t } = useI18n()
const withKeys = ACTIONS.filter((a) => a.shortcut !== null)
</script>

<template>
  <AppDialog :visible="visible" :header="t('shortcuts.title')" size="sm" @update:visible="emit('update:visible', $event)">
    <div class="stack" data-testid="shortcut-sheet">
      <p class="muted" data-testid="section-help">{{ t('shortcuts.help') }}</p>
      <ul class="plain-list">
        <li v-for="a in withKeys" :key="a.id" class="row" data-testid="shortcut-row" :data-action="a.id">
          <component :is="ACTION_ICONS[a.id]" :size="16" :stroke-width="2" aria-hidden="true" />
          <span>{{ t(a.label) }}</span>
          <kbd class="row-end mono">{{ a.shortcut }}</kbd>
        </li>
      </ul>
    </div>
  </AppDialog>
</template>
