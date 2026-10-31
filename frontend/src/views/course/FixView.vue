<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import Button from 'primevue/button'
import { ApiError, request } from '@/api/client'
import { blockAccept, blockItem, blockRewrite, courseHeld, courseRewriteHeld } from '@/api/endpoints'
import type { HeldBlock, HeldDoc } from '@/api/types'
import CourseHeader from '@/components/CourseHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import { notify } from '@/composables/notify'
import { useResource } from '@/composables/useResource'
import { useSessionStore } from '@/stores/session'

const { t } = useI18n()
const route = useRoute()
const session = useSessionStore()
const courseId = computed(() => String(route.params.id))
const held = useResource<HeldDoc>(`held:${courseId.value}`, (signal) => request<HeldDoc>(courseHeld(courseId.value), { signal }))
const working = ref<string | null>(null)
const editing = ref<string | null>(null)
const draft = ref('')

async function act(block: HeldBlock, kind: 'rewrite' | 'accept' | 'edit'): Promise<void> {
  working.value = `${kind}:${block.id}`
  try {
    if (kind === 'rewrite') await request(blockRewrite(block.id), { method: 'POST' })
    else if (kind === 'accept') await request(blockAccept(block.id), { method: 'POST' })
    else await request(blockItem(block.id), { method: 'PATCH', body: { brief: draft.value } })
    notify('success', t(`fix.done.${kind}`))
    editing.value = null
    await held.reload(true)
  } catch (cause) {
    notify('error', cause instanceof ApiError ? cause.message : String(cause))
  } finally {
    working.value = null
  }
}
async function rewriteAll(): Promise<void> {
  working.value = 'batch'
  try {
    await request(courseRewriteHeld(courseId.value), { method: 'POST' })
    await held.reload(true)
  } catch (cause) {
    notify('error', cause instanceof ApiError ? cause.message : String(cause))
  } finally {
    working.value = null
  }
}
function startEdit(block: HeldBlock): void {
  editing.value = block.id
  draft.value = block.brief
}
</script>

<template>
  <CourseHeader :course-id="courseId">
    <h2>{{ t('fix.title') }}</h2>
    <ResourceState :loading="held.loading.value" :error="held.error.value" :empty="(held.data.value?.blocks ?? []).length === 0" :empty-text="t('fix.empty')" @retry="held.reload()">
      <div v-if="held.data.value?.batch_quote_usd" class="row">
        <Button :label="t('fix.batch', { count: held.data.value.blocks.length, price: held.data.value.batch_quote_usd })" :loading="working === 'batch'" :aria-busy="working === 'batch'" :disabled="!session.can('course.edit')" data-testid="fix-batch" @click="rewriteAll" />
      </div>
      <ul class="plain-list">
        <li v-for="b in held.data.value?.blocks ?? []" :key="b.id" class="held-row" data-testid="held-row">
          <div class="stack">
            <strong>{{ b.topic_title }}</strong>
            <span>{{ b.hold_reason }}</span>
            <template v-if="editing === b.id">
              <textarea v-model="draft" rows="3" :aria-label="t('fix.editLabel')"></textarea>
              <div class="row">
                <Button :label="t('common.save')" size="small" :loading="working === `edit:${b.id}`" :aria-busy="working === `edit:${b.id}`" @click="act(b, 'edit')" />
                <Button :label="t('common.cancel')" size="small" severity="secondary" text @click="editing = null" />
              </div>
            </template>
            <div v-else class="row">
              <Button :label="t('fix.rewrite')" size="small" :loading="working === `rewrite:${b.id}`" :aria-busy="working === `rewrite:${b.id}`" :disabled="!session.can('course.edit')" @click="act(b, 'rewrite')" />
              <Button :label="t('fix.edit')" size="small" severity="secondary" :disabled="!session.can('course.edit')" @click="startEdit(b)" />
              <Button :label="t('fix.accept')" size="small" severity="secondary" :loading="working === `accept:${b.id}`" :aria-busy="working === `accept:${b.id}`" :disabled="!session.can('course.edit')" @click="act(b, 'accept')" />
            </div>
          </div>
          <blockquote class="passage">
            <p>{{ b.passage ?? t('fix.noPassage') }}</p>
            <cite v-if="b.address" class="mono">{{ b.address }}</cite>
          </blockquote>
        </li>
      </ul>
    </ResourceState>
  </CourseHeader>
</template>
