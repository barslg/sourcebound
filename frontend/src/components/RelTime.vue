<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{ value: string }>()
const { d, locale } = useI18n()
const date = computed(() => new Date(props.value))
const relative = computed(() => {
  const seconds = Math.round((date.value.getTime() - Date.now()) / 1000)
  const rtf = new Intl.RelativeTimeFormat(locale.value === 'pseudo' ? 'en' : locale.value, { numeric: 'auto' })
  const steps: Array<[Intl.RelativeTimeFormatUnit, number]> = [['year', 31536000], ['month', 2592000], ['day', 86400], ['hour', 3600], ['minute', 60]]
  for (const [unit, size] of steps) if (Math.abs(seconds) >= size) return rtf.format(Math.round(seconds / size), unit)
  return rtf.format(seconds, 'second')
})
</script>

<template>
  <time :datetime="value" :title="d(date, 'short')">{{ relative }}</time>
</template>
