<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Checkbox from 'primevue/checkbox'
import Column from 'primevue/column'
import type { DataTablePageEvent, DataTableSortEvent } from 'primevue/datatable'
import Select from 'primevue/select'
import Textarea from 'primevue/textarea'
import { ApiError, request } from '@/api/client'
import { ENDPOINTS, roleAssign, userBan, userItem, userUnban } from '@/api/endpoints'
import type { Role, UserRow } from '@/api/types'
import ActionButton from '@/components/ActionButton.vue'
import AppDialog from '@/components/AppDialog.vue'
import AppTable from '@/components/AppTable.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import FormField from '@/components/FormField.vue'
import NoValue from '@/components/NoValue.vue'
import RelTime from '@/components/RelTime.vue'
import RowMenu, { type RowMenuItem } from '@/components/RowMenu.vue'
import ScopePicker from '@/components/ScopePicker.vue'
import SearchField from '@/components/SearchField.vue'
import StatusChip from '@/components/StatusChip.vue'
import PageHeader from '@/components/PageHeader.vue'
import ResourceState from '@/components/ResourceState.vue'
import { firstReason, lacking } from '@/composables/reasons'
import { scopeTitles, useCourseIndex } from '@/composables/courseIndex'
import { userRole } from '@/api/endpoints'
import { notify, notifyError } from '@/composables/notify'
import { useResource } from '@/composables/useResource'
import { useUrlParam } from '@/composables/useUrlState'
import { useSessionStore } from '@/stores/session'

const PAGE_SIZE = 12
const { t } = useI18n()
const session = useSessionStore()
const users = useResource<UserRow[]>('users', (signal) => request<UserRow[]>(ENDPOINTS.users, { signal }))
const q = useUrlParam('q')
const pageParam = useUrlParam('page', '1')
const sortField = useUrlParam('sort', 'email')
const sortDir = useUrlParam('dir', 'asc')

const rows = computed(() => {
  const needle = q.value.trim().toLowerCase()
  return (users.data.value ?? []).filter((u) => !needle || u.email.toLowerCase().includes(needle) || u.display_name.toLowerCase().includes(needle))
})
const first = computed(() => (Math.max(1, Number(pageParam.value) || 1) - 1) * PAGE_SIZE)
const order = computed(() => (sortDir.value === 'desc' ? -1 : 1))

function onPage(e: DataTablePageEvent): void {
  pageParam.value = String(e.page + 1)
}
function onSort(e: DataTableSortEvent): void {
  sortField.value = typeof e.sortField === 'string' ? e.sortField : 'email'
  sortDir.value = e.sortOrder === -1 ? 'desc' : 'asc'
  pageParam.value = '1'
}
function onSearch(value: string | undefined): void {
  q.value = value ?? ''
  pageParam.value = '1'
}

const busy = ref(false)
const active = ref<UserRow | null>(null)
const mode = ref<'assign' | 'ban' | 'unban' | 'delete' | null>(null)
const formError = ref<string | null>(null)
const roleName = ref<string | null>(null)
const scopeTags = ref<string[]>([])
const reason = ref('')
const heirId = ref<string | null>(null)
const legacy = ref(false)
const roles = ref<Role[]>([])
const index = useCourseIndex()
const removing = ref<{ user: UserRow; role: string } | null>(null)
async function removeRole(): Promise<void> {
  const target = removing.value
  if (!target) return
  try {
    await request(userRole(target.user.id, target.role), { method: 'DELETE' })
    notify('success', t('users.roleRemoved'))
    removing.value = null
    await users.reload(true)
  } catch (cause) {
    notifyError(cause)
  }
}

function nameOf(u: UserRow): string {
  return u.display_name || u.email
}
function open(next: 'assign' | 'ban' | 'unban' | 'delete', user: UserRow): void {
  active.value = user
  mode.value = next
  formError.value = null
  roleName.value = null
  scopeTags.value = []
  reason.value = ''
  heirId.value = null
  legacy.value = false
  if (next === 'assign') {
    request<Role[]>(ENDPOINTS.roles)
      .then((list) => { roles.value = list })
      .catch((cause: unknown) => { formError.value = cause instanceof ApiError ? cause.message : String(cause) })
  }
}
function close(): void {
  mode.value = null
  active.value = null
}
async function submit(): Promise<void> {
  const user = active.value
  if (!user || !mode.value) return
  busy.value = true
  formError.value = null
  try {
    if (mode.value === 'assign' && roleName.value) {
      await request(roleAssign(roleName.value), { method: 'POST', body: { user_id: user.id, scope_tags: scopeTags.value } })
      notify('success', t('users.assigned'))
    } else if (mode.value === 'ban') {
      await request(userBan(user.id), { method: 'POST', body: { reason: reason.value.trim() } })
      notify('success', t('users.banned_ok'))
    } else if (mode.value === 'unban') {
      await request(userUnban(user.id), { method: 'POST' })
      notify('success', t('users.unbanned'))
    } else if (mode.value === 'delete') {
      await request(userItem(user.id), { method: 'DELETE', query: { heir_id: heirId.value ?? undefined, legacy: legacy.value ? true : undefined } })
      notify('success', t('users.deleted'))
    }
    close()
    await users.reload(true)
  } catch (cause) {
    formError.value = cause instanceof ApiError ? cause.message : String(cause)
  } finally {
    busy.value = false
  }
}
const heirOptions = computed(() => (users.data.value ?? []).filter((u) => u.id !== active.value?.id && !u.banned_at && !u.is_legacy))
const canSubmit = computed(() =>
  mode.value === 'assign' ? roleName.value !== null : mode.value === 'ban' ? reason.value.trim().length > 0 : true,
)
const closeIf = (v: boolean): void => { if (!v) close() }
const dialogTitle = computed(() => {
  const u = active.value
  if (!u) return ''
  return t(`users.${mode.value}Title`, { name: nameOf(u) })
})


function menuFor(u: UserRow): RowMenuItem[] {
  const you = u.id === session.me?.id && t('users.itsYou')
  return [
    u.banned_at
      ? { action: 'unban', label: t('action.unban'), reason: firstReason(lacking('user.ban')), run: () => open('unban', u) }
      : { action: 'ban', label: t('users.ban'), reason: firstReason(lacking('user.ban'), you), run: () => open('ban', u) },
    { action: 'delete', label: t('users.delete'), reason: firstReason(lacking('user.delete'), you), run: () => open('delete', u) },
  ]
}
</script>

<template>
  <PageHeader>
    <template #actions><router-link to="/invites" data-testid="users-invites">{{ t('users.invitePeople') }}</router-link></template>
  </PageHeader>
  <ResourceState
    :loading="users.loading.value"
    :error="users.error.value"
    :empty="(users.data.value ?? []).length === 0"
    :empty-text="t('users.empty')"
    @retry="users.reload()"
  >
    <div class="stack">
      <SearchField :model-value="q" :placeholder="t('users.searchPlaceholder')" data-testid="users-search" @update:model-value="onSearch" />
      <AppTable
        :value="rows"
        data-key="id"
        :paginator="rows.length > PAGE_SIZE"
        :rows="PAGE_SIZE"
        :first="first"
        :sort-field="sortField"
        :sort-order="order"
        removable-sort
        data-testid="users-table"
        @page="onPage"
        @sort="onSort"
      >
        <template #empty><span class="muted">{{ t('common.noResults') }}</span></template>
        <Column field="display_name" :header="t('users.name')" sortable>
          <template #body="{ data }"><template v-if="(data as UserRow).display_name">{{ (data as UserRow).display_name }}</template><NoValue v-else /></template>
        </Column>
        <Column field="email" :header="t('users.email')" sortable>
          <template #body="{ data }"><span class="mono">{{ (data as UserRow).email }}</span></template>
        </Column>
        <Column :header="t('users.roles')">
          <template #body="{ data }">
            <NoValue v-if="((data as UserRow).roles ?? []).length === 0" />
            <span v-for="g in (data as UserRow).roles ?? []" :key="g.name" class="chip" data-testid="role-chip">
              <router-link :to="`/roles#role-${g.name}`">{{ g.name }}<template v-if="g.scope_tags.length > 0"> ({{ scopeTitles(index.courses.value, g.scope_tags, t('scope.unknownCourse')).join(', ') }})</template></router-link>
              <ActionButton action="delete" icon-only size="small" :label="t('users.removeRole', { name: g.name })" :reason="lacking('role.assign')" data-testid="role-remove" @click="removing = { user: data as UserRow, role: g.name }" />
            </span>
          </template>
        </Column>
        <Column :header="t('users.status')">
          <template #body="{ data }">
            <StatusChip v-if="(data as UserRow).banned_at" tone="danger" :label="t('users.banned')" />
            <StatusChip v-else-if="(data as UserRow).is_legacy" tone="neutral" :label="t('users.legacy')" />
            <StatusChip v-else tone="ok" :label="t('users.active')" />
            <StatusChip v-if="(data as UserRow).is_superadmin" tone="info" :label="t('users.superadmin')" />
          </template>
        </Column>
        <Column field="created_at" :header="t('users.created')" sortable>
          <template #body="{ data }"><RelTime v-if="(data as UserRow).created_at" :value="(data as UserRow).created_at as string" /><NoValue v-else /></template>
        </Column>
        <Column :header="t('common.actions')" class="col-actions">
          <template #body="{ data }">
            <div class="row">
              <ActionButton action="assign" icon-only size="small" :label="t('users.assign')" :reason="lacking('role.assign')" @click="open('assign', data as UserRow)" />
              <RowMenu :label="t('users.moreFor', { name: nameOf(data as UserRow) })" :items="menuFor(data as UserRow)" />
            </div>
          </template>
        </Column>
      </AppTable>
    </div>
  </ResourceState>

  <AppDialog :visible="mode === 'assign'" :header="dialogTitle" @update:visible="closeIf">
    <form class="stack" @submit.prevent="submit">
      <FormField id="assign-role" :label="t('users.role')" :help="t('users.roleHint')" required :error="roleName === null ? t('users.pickRole') : null">
        <Select v-model="roleName" input-id="assign-role" :options="roles" option-label="name" option-value="name" :placeholder="t('users.rolePlaceholder')" />
      </FormField>
      <ScopePicker id="assign-scope" v-model="scopeTags" by="label" />
      <p v-if="formError" class="mono danger-text" role="alert" data-testid="form-error">{{ formError }}</p>
      <div class="row">
        <ActionButton action="cancel" @click="close" />
        <ActionButton type="submit" class="row-end" action="assign" :label="t('users.assign')" :loading="busy" :reason="canSubmit ? null : t('users.pickRole')" data-testid="dialog-submit" />
      </div>
    </form>
  </AppDialog>

  <ConfirmDialog :visible="removing !== null" :title="t('users.removeRoleTitle', { role: removing?.role ?? '', name: removing ? nameOf(removing.user) : '' })" :body="t('users.removeRoleBody')" action="delete" :label="t('users.removeRole', { name: removing?.role ?? '' })" @update:visible="(v: boolean) => { if (!v) removing = null }" @confirm="removeRole" />
  <ConfirmDialog :visible="mode === 'ban'" :title="dialogTitle" :body="t('users.banBody')" action="ban" :label="t('users.ban')" :busy="busy" :error="formError" :blocked-reason="canSubmit ? null : t('users.typeReason')" @update:visible="closeIf" @confirm="submit">
    <FormField id="ban-reason" :label="t('users.banReason')" :help="t('users.banReasonHint')" required :error="reason.trim().length === 0 ? t('form.isRequired') : null">
      <Textarea id="ban-reason" v-model="reason" rows="3" maxlength="1000" />
    </FormField>
  </ConfirmDialog>

  <ConfirmDialog :visible="mode === 'unban'" :title="dialogTitle" :body="active ? t('users.unbanBody', { name: nameOf(active) }) : ''" action="unban" :label="t('action.unban')" :busy="busy" :error="formError" @update:visible="closeIf" @confirm="submit" />

  <ConfirmDialog :visible="mode === 'delete'" :title="dialogTitle" :body="active ? t('users.deleteBody', { name: nameOf(active) }) : ''" action="delete" :label="t('users.delete')" :typed="active ? nameOf(active) : null" :busy="busy" :error="formError" @update:visible="closeIf" @confirm="submit">
    <FormField id="delete-heir" :label="t('users.heir')" :help="t('users.heirHint')">
      <Select v-model="heirId" input-id="delete-heir" :options="heirOptions" option-label="email" option-value="id" show-clear :placeholder="t('users.heirNone')" :disabled="legacy" />
    </FormField>
    <FormField id="delete-legacy" :label="t('users.legacyOption')" :help="t('users.legacyHint')">
      <Checkbox v-model="legacy" input-id="delete-legacy" binary :disabled="heirId !== null" />
    </FormField>
  </ConfirmDialog>
</template>
