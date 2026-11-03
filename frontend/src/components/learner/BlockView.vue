<script setup lang="ts">
import { computed, type Component } from 'vue'
import ChoiceBlock from './ChoiceBlock.vue'
import ClozeBlock from './ClozeBlock.vue'
import DiagramBlock from './DiagramBlock.vue'
import FigureBlock from './FigureBlock.vue'
import MatchingBlock from './MatchingBlock.vue'
import OrderingBlock from './OrderingBlock.vue'
import ReadingBlock from './ReadingBlock.vue'
import ScenarioBlock from './ScenarioBlock.vue'
import SpotErrorBlock from './SpotErrorBlock.vue'

defineOptions({ inheritAttrs: false })
const BY_TYPE: Record<string, Component> = {
  reading: ReadingBlock,
  choice: ChoiceBlock,
  cloze: ClozeBlock,
  figure: FigureBlock,
  diagram: DiagramBlock,
  matching: MatchingBlock,
  ordering: OrderingBlock,
  scenario_practice: ScenarioBlock,
  spot_the_error: SpotErrorBlock,
}
const props = defineProps<{ type: string; content: Record<string, unknown> }>()
const view = computed(() => BY_TYPE[props.type] ?? null)
</script>

<template>
  <component :is="view" v-if="view" v-bind="$attrs" :content="content" />
  <slot v-else />
</template>
