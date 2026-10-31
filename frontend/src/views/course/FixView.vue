<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { request } from '@/api/client'
import { blockAccept, blockItem, blockRewrite, courseHeld, coursePlan, courseRewriteHeld } from '@/api/endpoints'
import type { HeldBlock, HeldDoc } from '@/api/types'
import CourseHeader from '@/components/CourseHeader.vue'
import ActionButton from '@/components/ActionButton.vue'
import Queued from '@/components/Queued.vue'
import FormField from '@/components/FormField.vue'
import ResourceState from '@/components/ResourceState.vue'
import Section from '@/components/Section.vue'
import { usd } from '@/composables/labels'
import { notify, notifyError } from '@/composables/notify'
import type { PlanTree } from '@/composables/planTree'
import { lacking } from '@/composables/reasons'
import { useQueued } from '@/composables/useQueued'
import { useResource } from '@/composables/useResource'

const { t } = useI18n()
const route = useRoute()
const courseId = computed(() => String(route.params.id))
const header = ref<InstanceType<typeof CourseHeader> | null>(null)
const held = useResource<HeldDoc>(`held:${courseId.value}`, (signal) => request<HeldDoc>(courseHeld(courseId.value), { signal }))
const plan = useResource<PlanTree>(`plan:${courseId.value}`, (signal) => request<PlanTree>(coursePlan(courseId.value), { signal }))
const generated = computed(() => (plan.data.value?.modules ?? []).some((m) => m.topics.some((x) => x.blocks.some((b) => b.status !== 'draft'))))
const unread = computed(() => (plan.data.value?.modules ?? []).flatMap((m) => m.topics.flatMap((x) => x.blocks)).filter((b) => b.status === 'draft').length)
const working = ref<string | null>(null)
const editing = ref<string | null>(null)
const draft = ref('')
const queue = useQueued(async () => { await Promise.all([held.reload(true), plan.reload(true), header.value?.reload()]) })

async function act(block: HeldBlock, kind: 'rewrite' | 'accept' | 'edit'): Promise<void> {
  working.value = `${kind}:${block.id}`
  try {
    if (kind === 'rewrite') queue.track((await request<{ operation_id: string }>(blockRewrite(block.id), { method: 'POST' })).operation_id)
    else if (kind === 'accept') await request(blockAccept(block.id), { method: 'POST' })
    else await request(blockItem(block.id), { method: 'PATCH', body: { brief: draft.value } })
    notify('success', t(`fix.done.${kind}`))
    editing.value = null
    await Promise.all([held.reload(true), header.value?.reload()])
  } catch (cause) {
    notifyError(cause, kind === 'rewrite' ? undefined : () => void act(block, kind))
  } finally {
    working.value = null
  }
}
async function rewriteAll(): Promise<void> {
  working.value = 'batch'
  try {
    queue.track((await request<{ operation_id: string }>(courseRewriteHeld(courseId.value), { method: 'POST' })).operation_id)
    notify('success', t('fix.done.batch'))
    await held.reload(true)
  } catch (cause) {
    notifyError(cause)
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
  <CourseHeader ref="header" :course-id="courseId">
    <div id="held-list" class="stack">
      <Section :title="t('fix.title')" :help="t('fix.help')" />
      <ResourceState :loading="held.loading.value" :error="held.error.value" :empty="(held.data.value?.blocks ?? []).length === 0" :empty-text="!generated ? t('fix.notGenerated') : unread > 0 ? t('fix.unread', { count: t('count.block', unread) }) : t('fix.empty')" @retry="held.reload()">
        <template #empty-actions>
          <router-link :to="generated && unread === 0 ? `/courses/${courseId}/publish` : `/courses/${courseId}/plan#generate`" data-testid="fix-exit">{{ generated && unread === 0 ? t('fix.goPublish') : t('fix.goGenerate') }}</router-link>
        </template>
        <div v-if="held.data.value?.batch_quote_usd" class="row">
          <ActionButton action="rewrite" :label="t('fix.batch', { count: held.data.value.blocks.length, price: usd(held.data.value.batch_quote_usd) })" :loading="working === 'batch'" :reason="lacking('course.generate')" data-testid="fix-batch" @click="rewriteAll" />
        </div>
        <Queued v-if="queue.queued.value" />
        <ul class="plain-list">
          <li v-for="b in held.data.value?.blocks ?? []" :id="`held-${b.id}`" :key="b.id" class="held-row" data-testid="held-row">
            <div class="stack">
              <router-link :to="`/courses/${courseId}/plan`"><strong>{{ b.topic_title }}</strong></router-link>
              <span>{{ b.hold_reason }}</span>
              <template v-if="editing === b.id">
                <FormField :id="`brief-${b.id}`" :label="t('fix.editLabel')" :help="t('fix.editHint')" required :error="draft.trim().length === 0 ? t('form.isRequired') : null">
                  <textarea :id="`brief-${b.id}`" v-model="draft" rows="3"></textarea>
                </FormField>
                <div class="row">
                  <ActionButton action="save" size="small" :label="t('fix.saveBrief')" :loading="working === `edit:${b.id}`" :reason="draft.trim().length === 0 ? t('fix.typeBrief') : null" @click="act(b, 'edit')" />
                  <ActionButton action="cancel" size="small" @click="editing = null" />
                </div>
              </template>
              <div v-else class="row">
                <ActionButton action="rewrite" :label="t('fix.rewrite')" size="small" :loading="working === `rewrite:${b.id}`" :reason="lacking('course.generate')" @click="act(b, 'rewrite')" />
                <ActionButton action="edit" :label="t('fix.edit')" size="small" :reason="lacking('course.edit')" @click="startEdit(b)" />
                <ActionButton action="accept" :label="t('fix.accept')" size="small" :loading="working === `accept:${b.id}`" :reason="lacking('course.edit')" @click="act(b, 'accept')" />
              </div>
            </div>
            <blockquote class="passage">
              <p>{{ b.passage || t('fix.noPassage') }}</p>
              <cite v-if="b.address" class="mono">{{ b.address }}</cite>
              <router-link :to="`/courses/${courseId}/sources#sources-list`">{{ t('fix.openSources') }}</router-link>
            </blockquote>
          </li>
        </ul>
      </ResourceState>
    </div>
  </CourseHeader>
</template>
