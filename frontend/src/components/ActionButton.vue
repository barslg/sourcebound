<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from 'primevue/button'
import { ACTION_ICONS } from '@/ui/actionIcons'
import { metaOf, type ActionId } from '@/ui/actionMeta'

defineOptions({ inheritAttrs: false })
const props = defineProps<{
  action: ActionId
  label?: string
  reason?: string | null
  loading?: boolean
  size?: 'small'
  iconOnly?: boolean
  solid?: boolean
  accent?: boolean
  type?: 'button' | 'submit'
}>()
const { t } = useI18n()
const meta = computed(() => metaOf(props.action))
const text = computed(() => props.label ?? t(meta.value.label))
const icon = computed(() => ACTION_ICONS[props.action])
const tip = computed(() => (props.iconOnly || meta.value.shortcut ? (meta.value.shortcut ? `${text.value} (${meta.value.shortcut})` : text.value) : ''))
function showTip(event: FocusEvent): void {
  const el = event.currentTarget as HTMLElement
  if (tip.value !== '' && el.matches(':focus-visible')) el.dispatchEvent(new MouseEvent('mouseenter'))
}
function hideTip(event: FocusEvent): void {
  if (tip.value !== '') (event.currentTarget as HTMLElement).dispatchEvent(new MouseEvent('mouseleave'))
}
const look = computed(() => {
  const kind = meta.value.kind
  if (kind === 'destructive') return props.solid ? { severity: 'danger' as const } : { severity: 'danger' as const, outlined: true }
  if (kind === 'secondary') return { severity: 'secondary' as const }
  if (kind === 'navigation') return { severity: 'secondary' as const, text: true }
  return {}
})
</script>

<template>
  <span class="guarded">
    <Button
      v-tooltip.bottom="{ value: tip, showDelay: 400, disabled: tip === '' }"
      v-bind="{ ...$attrs, ...look }"
      :label="iconOnly ? undefined : text"
      :aria-label="iconOnly ? text : undefined"
      :loading="loading"
      :size="size"
      :type="type ?? 'button'"
      :aria-busy="loading"
      :disabled="Boolean(reason)"
      :class="{ 'btn-accent': accent }"
      :data-kind="meta.kind"
      :data-action="action"
      :data-size="size ?? 'default'"
      @focus="showTip"
      @blur="hideTip"
    >
      <template #icon><component :is="icon" :size="16" :stroke-width="2" aria-hidden="true" /></template>
    </Button>
    <slot name="after" />
    <span v-if="reason" class="hint" data-testid="reason">{{ reason }}</span>
  </span>
</template>
