<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Checkbox from 'primevue/checkbox'
import Column from 'primevue/column'
import InputText from 'primevue/inputtext'
import { ApiError, request } from '@/api/client'
import { ENDPOINTS, roleItem } from '@/api/endpoints'
import type { Permission, Role, UserRow } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import AppDialog from '@/components/AppDialog.vue'
import AppTable from '@/components/AppTable.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import FormField from '@/components/FormField.vue'
import NoValue from '@/components/NoValue.vue'
import RowMenu from '@/components/RowMenu.vue'
import ScopePicker from '@/components/ScopePicker.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import { humanize } from '@/composables/labels'
import { firstReason, lacking } from '@/composables/reasons'
import { notify } from '@/composables/notify'
import { useResource } from '@/composables/useResource'
import { useSessionStore } from '@/stores/session'

const { t } = useI18n()
const session = useSessionStore()
const roles = useResource<Role[]>('roles', (signal) => request<Role[]>(ENDPOINTS.roles, { signal }))

const permissions = useResource<Permission[]>('permissions', (signal) => request<Permission[]>(ENDPOINTS.permissions, { signal }))
const people = useResource<UserRow[]>('users', (signal) => request<UserRow[]>(ENDPOINTS.users, { signal }))
const holders = (name: string): UserRow[] => (people.data.value ?? []).filter((u) => (u.roles ?? []).some((g) => g.name === name))
const labelOf = (code: string): { label: string; description: string } => {
  const p = (permissions.data.value ?? []).find((x) => x.code === code)
  return { label: p?.label ?? humanize(code), description: p?.description ?? '' }
}
const BUILT_IN = 'administrator'
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
const scopeTags = ref<string[]>([])
const busy = ref(false)
const formError = ref<string | null>(null)
const askDiscard = ref(false)
const initial = ref('')

const snapshot = (): string => JSON.stringify([name.value, [...codes.value].sort(), [...scopeTags.value].sort()])
const dirty = computed(() => mode.value !== 'delete' && mode.value !== null && snapshot() !== initial.value)

function open(next: 'edit' | 'create' | 'delete', role: Role | null): void {
  mode.value = next
  target.value = role
  name.value = role?.name ?? ''
  codes.value = [...(role?.permission_codes ?? [])]
  scopeTags.value = [...(role?.scope_tags ?? [])]
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
const formVisible = computed(() => mode.value === 'create' || mode.value === 'edit')
const closeIf = (v: boolean): void => { if (!v) requestClose() }
const builtInReason = (r: Role): string | null => (r.name === BUILT_IN ? t('roles.builtIn') : null)

async function submit(): Promise<void> {
  busy.value = true
  formError.value = null
  try {
    if (mode.value === 'create') {
      await request(ENDPOINTS.roles, { method: 'POST', body: { name: name.value.trim(), permission_codes: codes.value, scope_tags: scopeTags.value } })
      notify('success', t('roles.created'))
    } else if (mode.value === 'edit' && target.value) {
      await request(roleItem(target.value.name), { method: 'PUT', body: { permission_codes: codes.value, scope_tags: scopeTags.value } })
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
</script>

<template>
  <PageHeader>
    <template #actions><ActionButton action="create" :label="t('roles.new')" :reason="lacking('role.edit')" data-testid="role-new" @click="open('create', null)" /></template>
  </PageHeader>
  <ResourceState
    :loading="roles.loading.value"
    :error="roles.error.value"
    :empty="(roles.data.value ?? []).length === 0"
    :empty-text="t('roles.empty')"
    @retry="roles.reload()"
  >
    <AppTable :value="roles.data.value ?? []" data-key="name" data-testid="roles-table">
      <template #empty><span class="muted">{{ t('roles.empty') }}</span></template>
      <Column field="name" :header="t('roles.name')"><template #body="{ data }"><span :id="`role-${(data as Role).name}`">{{ (data as Role).name }}</span></template></Column>
      <Column :header="t('roles.permissions')"><template #body="{ data }">{{ t('count.permission', (data as Role).permission_codes.length) }}</template></Column>
      <Column :header="t('roles.holders')">
        <template #body="{ data }">
          <NoValue v-if="holders((data as Role).name).length === 0" />
          <router-link v-for="u in holders((data as Role).name)" :key="u.id" class="chip" :to="`/users?q=${encodeURIComponent(u.email)}`">{{ u.display_name || u.email }}</router-link>
        </template>
      </Column>
      <Column :header="t('roles.scope')">
        <template #body="{ data }">
          <span v-if="(data as Role).scope_tags.length === 0" class="muted">{{ t('roles.global') }}</span>
          <span v-for="tag in (data as Role).scope_tags" :key="tag" class="chip mono">{{ tag }}</span>
        </template>
      </Column>
      <Column :header="t('common.actions')" class="col-actions">
        <template #body="{ data }">
          <div class="row">
            <ActionButton action="edit" icon-only size="small" :label="t('roles.edit')" :reason="firstReason(lacking('role.edit'), builtInReason(data as Role))" @click="open('edit', data as Role)" />
            <RowMenu :label="t('roles.moreFor', { name: (data as Role).name })" :items="[{ action: 'delete', label: t('roles.deleteButton'), reason: firstReason(lacking('role.edit'), builtInReason(data as Role)), run: () => open('delete', data as Role) }]" />
          </div>
        </template>
      </Column>
    </AppTable>
  </ResourceState>

  <AppDialog :visible="formVisible" :header="title" size="lg" @update:visible="closeIf">
    <form class="stack" @submit.prevent="submit">
      <FormField id="role-name" :label="t('roles.name')" :help="t('roles.nameHint')" required :error="name.trim().length === 0 ? t('roles.typeName') : null">
        <InputText id="role-name" v-model="name" :disabled="mode === 'edit'" />
      </FormField>
      <div class="perm-groups">
        <fieldset v-for="[group, list] in catalogue" :key="group" class="perm-group">
          <legend>{{ humanize(group) }}</legend>
          <div v-for="code in list" :key="code" class="perm-check">
            <Checkbox v-model="codes" :input-id="`perm-${code}`" :value="code" />
            <label :for="`perm-${code}`">{{ labelOf(code).label }} <span class="hint">{{ labelOf(code).description }}</span></label>
          </div>
        </fieldset>
      </div>
      <ScopePicker id="role-scope" v-model="scopeTags" by="label" />
      <div v-if="askDiscard" class="confirm-bar" role="alert" data-testid="discard-bar">
        <strong>{{ t('common.unsavedTitle') }}</strong> {{ t('common.unsavedBody') }}
        <div class="row">
          <ActionButton action="keep-editing" size="small" @click="askDiscard = false" />
          <ActionButton action="discard" size="small" solid data-testid="discard" @click="close" />
        </div>
      </div>
      <p v-if="formError" class="mono danger-text" role="alert" data-testid="form-error">{{ formError }}</p>
      <div class="row">
        <ActionButton action="cancel" @click="requestClose" />
        <ActionButton v-if="mode === 'create'" type="submit" class="row-end" action="create" :label="t('roles.createButton')" :loading="busy" :reason="name.trim().length === 0 ? t('roles.typeName') : null" data-testid="dialog-submit" />
        <ActionButton v-else type="submit" class="row-end" action="save" :label="t('roles.saveButton')" :loading="busy" data-testid="dialog-submit" />
      </div>
    </form>
  </AppDialog>

  <ConfirmDialog :visible="mode === 'delete'" :title="title" :body="t('roles.deleteBody')" action="delete" :label="t('roles.deleteButton')" :typed="target?.name ?? null" :busy="busy" :error="formError" @update:visible="closeIf" @confirm="submit" />
</template>
