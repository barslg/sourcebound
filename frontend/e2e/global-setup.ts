import { sessions } from './fixtures'

const API = 'http://127.0.0.1:8010/api/v1'

async function call(who: 'admin', method: string, path: string, body?: unknown): Promise<Response> {
  const s = sessions()[who]
  return fetch(API + path, {
    method,
    headers: { 'Content-Type': 'application/json', Cookie: `muse_session=${s.token}`, 'X-CSRF-Token': s.csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export default async function globalSetup(): Promise<void> {
  const s = sessions()
  const roles: [string, string[]][] = [
    ['student', ['section.courses']],
    ['operator', ['section.courses', 'operation.run']],
  ]
  for (const [name, codes] of roles) {
    const res = await call('admin', 'POST', '/roles', { name, permission_codes: codes })
    if (!res.ok && res.status !== 409) throw new Error(`role ${name}: ${res.status} ${await res.text()}`)
  }
  const assign: [string, string][] = [['student', s.student.id], ['operator', s.other.id]]
  for (const [role, id] of assign) {
    const res = await call('admin', 'POST', `/roles/${role}/assign`, { user_id: id, scope_tags: [] })
    if (!res.ok) throw new Error(`assign ${role}: ${res.status} ${await res.text()}`)
  }
}
