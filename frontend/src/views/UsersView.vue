<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Column from 'primevue/column'
import DataTable, { type DataTablePageEvent, type DataTableSortEvent } from 'primevue/datatable'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import Textarea from 'primevue/textarea'
import { ApiError, request } from '@/api/client'
import { ENDPOINTS, roleAssign, userBan, userItem } from '@/api/endpoints'
import type { Role, UserRow } from '@/api/types'
import ResourceState from '@/components/ResourceState.vue'
import { notify } from '@/composables/notify'
import { parseTags } from '@/composables/tags'
import { useResource } from '@/composables/useResource'
import { useUrlParam } from '@/composables/useUrlState'
import { useSessionStore } from '@/stores/session'

const PAGE_SIZE = 10
const { t, d } = useI18n()
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
const mode = ref<'assign' | 'ban' | 'delete' | null>(null)
const formError = ref<string | null>(null)
const roleName = ref<string | null>(null)
const scopeText = ref('')
const reason = ref('')
const heirId = ref<string | null>(null)
const legacy = ref(false)
const roles = ref<Role[]>([])

function nameOf(u: UserRow): string {
  return u.display_name || u.email
}
function open(next: 'assign' | 'ban' | 'delete', user: UserRow): void {
  active.value = user
  mode.value = next
  formError.value = null
  roleName.value = null
  scopeText.value = ''
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
      await request(roleAssign(roleName.value), { method: 'POST', body: { user_id: user.id, scope_tags: parseTags(scopeText.value) } })
      notify('success', t('users.assigned'))
    } else if (mode.value === 'ban') {
      await request(userBan(user.id), { method: 'POST', body: { reason: reason.value.trim() } })
      notify('success', t('users.banned_ok'))
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
const visible = computed({ get: () => mode.value !== null, set: (v: boolean) => { if (!v) close() } })
const dialogTitle = computed(() => {
  const u = active.value
  if (!u) return ''
  return t(mode.value === 'assign' ? 'users.assignTitle' : mode.value === 'ban' ? 'users.banTitle' : 'users.deleteTitle', { name: nameOf(u) })
})
</script>

<template>
  <h1 class="page-title">{{ t('users.title') }}</h1>
  <ResourceState
    :loading="users.loading.value"
    :error="users.error.value"
    :empty="(users.data.value ?? []).length === 0"
    :empty-text="t('users.empty')"
    @retry="users.reload()"
  >
    <div class="stack">
      <InputText
        :model-value="q"
        type="search"
        :placeholder="t('users.searchPlaceholder')"
        :aria-label="t('common.search')"
        data-testid="users-search"
        @update:model-value="onSearch"
      />
      <DataTable
        :value="rows"
        data-key="id"
        paginator
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
        <Column field="display_name" :header="t('users.name')" sortable />
        <Column field="email" :header="t('users.email')" sortable>
          <template #body="{ data }"><span class="mono">{{ (data as UserRow).email }}</span></template>
        </Column>
        <Column :header="t('users.status')">
          <template #body="{ data }">
            <span v-if="(data as UserRow).banned_at" class="chip chip-warn">{{ t('users.banned') }}</span>
            <span v-else-if="(data as UserRow).is_legacy" class="chip">{{ t('users.legacy') }}</span>
            <span v-else class="chip">{{ t('users.active') }}</span>
            <span v-if="(data as UserRow).is_superadmin" class="chip">{{ t('users.superadmin') }}</span>
          </template>
        </Column>
        <Column field="created_at" :header="t('users.created')" sortable>
          <template #body="{ data }">{{ (data as UserRow).created_at ? d(new Date((data as UserRow).created_at as string), 'short') : t('common.never') }}</template>
        </Column>
        <Column :header="t('common.actions')">
          <template #body="{ data }">
            <div class="row">
              <Button
                :label="t('users.assign')"
                size="small"
                severity="secondary"
                :disabled="!session.can('role.assign')"
                :title="session.can('role.assign') ? undefined : t('common.reason', { code: 'role.assign' })"
                @click="open('assign', data as UserRow)"
              />
              <Button
                :label="t('users.ban')"
                size="small"
                severity="secondary"
                :disabled="!session.can('user.ban') || Boolean((data as UserRow).banned_at)"
                :title="session.can('user.ban') ? undefined : t('common.reason', { code: 'user.ban' })"
                @click="open('ban', data as UserRow)"
              />
              <Button
                :label="t('users.delete')"
                size="small"
                severity="danger"
                outlined
                :disabled="!session.can('user.delete')"
                :title="session.can('user.delete') ? undefined : t('common.reason', { code: 'user.delete' })"
                @click="open('delete', data as UserRow)"
              />
            </div>
          </template>
        </Column>
      </DataTable>
    </div>
  </ResourceState>

  <Dialog v-model:visible="visible" modal :header="dialogTitle" :style="{ width: 'min(520px, 95vw)' }">
    <form class="stack" @submit.prevent="submit">
      <template v-if="mode === 'assign'">
        <div class="field">
          <label for="assign-role">{{ t('users.role') }}</label>
          <Select v-model="roleName" input-id="assign-role" :options="roles" option-label="name" option-value="name" :placeholder="t('users.rolePlaceholder')" />
        </div>
        <div class="field">
          <label for="assign-scope">{{ t('users.scope') }}</label>
          <InputText id="assign-scope" v-model="scopeText" />
          <span class="hint">{{ t('common.tagsHint') }}</span>
        </div>
      </template>
      <template v-else-if="mode === 'ban'">
        <p>{{ t('users.banBody') }}</p>
        <div class="field">
          <label for="ban-reason">{{ t('users.banReason') }}</label>
          <Textarea id="ban-reason" v-model="reason" rows="3" maxlength="1000" />
          <span class="hint">{{ t('users.banReasonHint') }}</span>
        </div>
      </template>
      <template v-else-if="mode === 'delete' && active">
        <p>{{ t('users.deleteBody', { name: nameOf(active) }) }}</p>
        <div class="field">
          <label for="delete-heir">{{ t('users.heir') }}</label>
          <Select v-model="heirId" input-id="delete-heir" :options="heirOptions" option-label="email" option-value="id" show-clear :placeholder="t('users.heirNone')" :disabled="legacy" />
          <span class="hint">{{ t('users.heirHint') }}</span>
        </div>
        <div class="row">
          <Checkbox v-model="legacy" input-id="delete-legacy" binary :disabled="heirId !== null" />
          <label for="delete-legacy">{{ t('users.legacyOption') }}</label>
        </div>
      </template>
      <p v-if="formError" class="mono danger-text" role="alert" data-testid="form-error">{{ formError }}</p>
      <div class="row">
        <Button type="button" :label="t('common.cancel')" severity="secondary" text @click="close" />
        <Button
          type="submit"
          class="row-end"
          :label="mode === 'delete' ? t('users.delete') : mode === 'ban' ? t('users.ban') : t('users.assign')"
          :severity="mode === 'delete' ? 'danger' : undefined"
          :loading="busy"
          :aria-busy="busy"
          :disabled="!canSubmit"
          data-testid="dialog-submit"
        />
      </div>
    </form>
  </Dialog>
</template>
