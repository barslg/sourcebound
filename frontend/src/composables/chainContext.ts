import type { InjectionKey, Ref } from 'vue'
import type { CallTreeNode } from '@/api/types'
import type { ChainIndex, Shown } from '@/composables/chainTree'

export interface ChainContext {
  courseId: Ref<string>
  index: Ref<ChainIndex>
  open: Ref<Set<string>>
  shown: Ref<Shown | null>
  setOpen: (id: string, value: boolean) => void
  goTo: (id: string) => void
  showCall: (node: CallTreeNode) => void
}

export const CHAIN_CONTEXT: InjectionKey<ChainContext> = Symbol('chain-context')
