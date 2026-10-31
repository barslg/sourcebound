<script setup lang="ts">
import { computed, type Component } from 'vue'
import ChoiceBlock from './ChoiceBlock.vue'
import ClozeBlock from './ClozeBlock.vue'
import FigureBlock from './FigureBlock.vue'
import MatchingBlock from './MatchingBlock.vue'
import ReadingBlock from './ReadingBlock.vue'
import ScenarioBlock from './ScenarioBlock.vue'
import SpotErrorBlock from './SpotErrorBlock.vue'

const BY_TYPE: Record<string, Component> = {
  reading: ReadingBlock,
  choice: ChoiceBlock,
  cloze: ClozeBlock,
  figure: FigureBlock,
  matching: MatchingBlock,
  scenario_practice: ScenarioBlock,
  spot_the_error: SpotErrorBlock,
}
const props = defineProps<{ type: string; content: Record<string, unknown> }>()
const view = computed(() => BY_TYPE[props.type] ?? null)
</script>

<template>
  <component :is="view" v-if="view" :content="content" />
  <slot v-else />
</template>
