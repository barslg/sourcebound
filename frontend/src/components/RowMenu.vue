<script setup lang="ts">
import { computed, ref } from 'vue'
import Menu from 'primevue/menu'
import ActionButton from '@/components/ActionButton.vue'
import { ACTION_ICONS } from '@/ui/actionIcons'
import type { ActionId } from '@/ui/actionMeta'

export interface RowMenuItem { action: ActionId; label: string; reason?: string | null; run: () => void }
const props = defineProps<{ label: string; items: RowMenuItem[] }>()
const menu = ref<InstanceType<typeof Menu> | null>(null)
const model = computed(() => props.items.map((i) => ({ label: i.label, action: i.action, reason: i.reason ?? null, disabled: Boolean(i.reason), command: () => i.run() })))
</script>

<template>
  <ActionButton action="more" icon-only size="small" :label="label" aria-haspopup="menu" @click="menu?.toggle($event)" />
  <Menu ref="menu" popup :model="model" class="row-menu">
    <template #item="{ item, props: p }">
      <a class="row-menu-item" v-bind="p.action" :class="{ 'row-menu-danger': item.action === 'delete' || item.action === 'ban' }">
        <component :is="ACTION_ICONS[item.action as ActionId]" :size="16" :stroke-width="2" aria-hidden="true" />
        <span class="row-menu-text">{{ item.label }}<span v-if="item.reason" class="hint row-menu-reason" data-testid="reason">{{ item.reason }}</span></span>
      </a>
    </template>
  </Menu>
</template>
