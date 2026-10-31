<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import { ApiError, request } from '@/api/client'
import { ENDPOINTS, roleItem } from '@/api/endpoints'
import type { Role } from '@/api/types'
import ResourceState from '@/components/ResourceState.vue'
import { notify } from '@/composables/notify'
import { parseTags } from '@/composables/tags'
import { useResource } from '@/composables/useResource'
import { useSessionStore } from '@/stores/session'

const { t } = useI18n()
const session = useSessionStore()
const roles = useResource<Role[]>('roles', (signal) => request<Role[]>(ENDPOINTS.roles, { signal }))

const catalogue = computed(() => {
  const all = new Set<string>()
  for (const r of roles.data.value ?? []) for (const c of r.permission_codes) all.add(c)
  for (const c of session.permissions) all.add(c)
  const groups = new Map<string, string[]>()
  for (const code of [...all].sort()) {
    const g = code.split('.')[0] ?? code
    groups.set(g, [...(groups.get(g) ?? []), code])
  }
  return [...groups.entries()]
})

const mode = ref<'edit' | 'create' | 'delete' | null>(null)
const target = ref<Role | null>(null)
const name = ref('')
const codes = ref<string[]>([])
const scopeText = ref('')
const busy = ref(false)
const formError = ref<string | null>(null)
const askDiscard = ref(false)
const initial = ref('')

const snapshot = (): string => JSON.stringify([name.value, [...codes.value].sort(), parseTags(scopeText.value)])
const dirty = computed(() => mode.value !== 'delete' && mode.value !== null && snapshot() !== initial.value)

function open(next: 'edit' | 'create' | 'delete', role: Role | null): void {
  mode.value = next
  target.value = role
  name.value = role?.name ?? ''
  codes.value = [...(role?.permission_codes ?? [])]
  scopeText.value = (role?.scope_tags ?? []).join(', ')
  formError.value = null
  askDiscard.value = false
  initial.value = snapshot()
}
function close(): void {
  mode.value = null
  target.value = null
  askDiscard.value = false
}
function requestClose(): void {
  if (dirty.value && !askDiscard.value) askDiscard.value = true
  else close()
}
const visible = computed({ get: () => mode.value !== null, set: (v: boolean) => { if (!v) requestClose() } })

async function submit(): Promise<void> {
  busy.value = true
  formError.value = null
  try {
    if (mode.value === 'create') {
      await request(ENDPOINTS.roles, { method: 'POST', body: { name: name.value.trim(), permission_codes: codes.value, scope_tags: parseTags(scopeText.value) } })
      notify('success', t('roles.created'))
    } else if (mode.value === 'edit' && target.value) {
      await request(roleItem(target.value.name), { method: 'PUT', body: { permission_codes: codes.value, scope_tags: parseTags(scopeText.value) } })
      notify('success', t('roles.updated'))
    } else if (mode.value === 'delete' && target.value) {
      await request(roleItem(target.value.name), { method: 'DELETE' })
      notify('success', t('roles.deleted'))
    }
    close()
    await roles.reload(true)
  } catch (cause) {
    formError.value = cause instanceof ApiError ? cause.message : String(cause)
  } finally {
    busy.value = false
  }
}
const title = computed(() =>
  mode.value === 'create' ? t('roles.newTitle') : mode.value === 'edit' ? t('roles.editTitle', { name: target.value?.name ?? '' }) : t('roles.deleteTitle', { name: target.value?.name ?? '' }),
)
const crudText = (r: Role): string => Object.entries(r.crud).map(([k, v]) => `${k}: ${v.join('/')}`).join('; ')
</script>

<template>
  <div class="row">
    <h1 class="page-title">{{ t('roles.title') }}</h1>
    <Button
      class="row-end"
      :label="t('roles.new')"
      :disabled="!session.can('role.edit')"
      :title="session.can('role.edit') ? undefined : t('common.reason', { code: 'role.edit' })"
      data-testid="role-new"
      @click="open('create', null)"
    />
  </div>
  <ResourceState
    :loading="roles.loading.value"
    :error="roles.error.value"
    :empty="(roles.data.value ?? []).length === 0"
    :empty-text="t('roles.empty')"
    @retry="roles.reload()"
  >
    <DataTable :value="roles.data.value ?? []" data-key="name" data-testid="roles-table">
      <Column field="name" :header="t('roles.name')"><template #body="{ data }"><span class="mono">{{ (data as Role).name }}</span></template></Column>
      <Column :header="t('roles.permissions')"><template #body="{ data }">{{ t('roles.count', { count: (data as Role).permission_codes.length }) }}</template></Column>
      <Column :header="t('roles.scope')">
        <template #body="{ data }">
          <span v-if="(data as Role).scope_tags.length === 0" class="muted">{{ t('roles.global') }}</span>
          <span v-for="tag in (data as Role).scope_tags" :key="tag" class="chip mono">{{ tag }}</span>
        </template>
      </Column>
      <Column :header="t('roles.crud')">
        <template #body="{ data }"><span class="mono">{{ crudText(data as Role) || t('roles.noCrud') }}</span></template>
      </Column>
      <Column :header="t('common.actions')">
        <template #body="{ data }">
          <div class="row">
            <Button :label="t('roles.edit')" size="small" severity="secondary" @click="open('edit', data as Role)" />
            <Button :label="t('common.delete')" size="small" severity="danger" outlined @click="open('delete', data as Role)" />
          </div>
        </template>
      </Column>
    </DataTable>
  </ResourceState>

  <Dialog v-model:visible="visible" modal :header="title" :style="{ width: 'min(720px, 95vw)' }">
    <form class="stack" @submit.prevent="submit">
      <p v-if="mode === 'delete'">{{ t('roles.deleteBody') }}</p>
      <template v-else>
        <div class="field">
          <label for="role-name">{{ t('roles.name') }}</label>
          <InputText id="role-name" v-model="name" :disabled="mode === 'edit'" class="mono" />
          <span class="hint">{{ t('roles.nameHint') }}</span>
        </div>
        <div class="perm-groups">
          <fieldset v-for="[group, list] in catalogue" :key="group" class="perm-group">
            <legend>{{ group }}</legend>
            <div v-for="code in list" :key="code" class="perm-check">
              <Checkbox v-model="codes" :input-id="`perm-${code}`" :value="code" />
              <label :for="`perm-${code}`" class="mono">{{ code }}</label>
            </div>
          </fieldset>
        </div>
        <div class="field">
          <label for="role-scope">{{ t('roles.scope') }}</label>
          <InputText id="role-scope" v-model="scopeText" />
          <span class="hint">{{ t('common.tagsHint') }}</span>
        </div>
      </template>
      <div v-if="askDiscard" class="confirm-bar" role="alert" data-testid="discard-bar">
        <strong>{{ t('common.unsavedTitle') }}</strong> {{ t('common.unsavedBody') }}
        <div class="row">
          <Button type="button" :label="t('common.keepEditing')" size="small" severity="secondary" @click="askDiscard = false" />
          <Button type="button" :label="t('common.discard')" size="small" severity="danger" data-testid="discard" @click="close" />
        </div>
      </div>
      <p v-if="formError" class="mono danger-text" role="alert" data-testid="form-error">{{ formError }}</p>
      <div class="row">
        <Button type="button" :label="t('common.cancel')" severity="secondary" text @click="requestClose" />
        <Button
          type="submit"
          class="row-end"
          :label="mode === 'delete' ? t('common.delete') : mode === 'create' ? t('common.create') : t('common.save')"
          :severity="mode === 'delete' ? 'danger' : undefined"
          :loading="busy"
          :aria-busy="busy"
          :disabled="mode !== 'delete' && name.trim().length === 0"
          data-testid="dialog-submit"
        />
      </div>
    </form>
  </Dialog>
</template>
