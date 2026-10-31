export interface Me {
  id: string
  email: string
  display_name: string
  avatar_url: string
  is_superadmin: boolean
  roles: string[]
  permissions: string[]
  scope_tags: string[]
}
export interface UserRow {
  id: string
  email: string
  display_name: string
  is_superadmin: boolean
  is_legacy: boolean
  banned_at: string | null
  banned_reason: string
  created_at: string | null
}
export interface Role {
  name: string
  permission_codes: string[]
  crud: Record<string, string[]>
  scope_tags: string[]
}
export interface Invite {
  token: string
  course_tags: string[]
  max_uses: number | null
  used_count: number
  expires_at: string | null
  revoked_at: string | null
}
export interface Operation {
  operation_id: string
  kind: string
  status: string
  progress: number
  result: Record<string, unknown> | null
  error: string | null
}
export interface AcceptResult { scopes: string[] }
