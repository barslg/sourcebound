import { ids, sessions } from './fixtures'

const API = `http://127.0.0.1:${process.env.E2E_BACKEND_PORT ?? '8010'}/api/v1`

async function call(who: 'admin' | 'author', method: string, path: string, body?: unknown): Promise<Response> {
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
    ['student', ['learn.read', 'learn.answer', 'learn.ask']],
    ['operator', ['learn.read', 'operation.run']],
    ['author', ['section.courses', 'course.edit', 'course.create', 'source.upload']],
    ['editor-only', ['course.edit']],
  ]
  for (const [name, codes] of roles) {
    const res = await call('admin', 'POST', '/roles', { name, permission_codes: codes })
    if (!res.ok && res.status !== 409) throw new Error(`role ${name}: ${res.status} ${await res.text()}`)
  }
  const learn = ids().learn
  const assign: [string, string, string[]][] = [['student', s.student.id, [learn]], ['student', s.admin.id, [learn]], ['operator', s.other.id, []], ['author', s.author.id, []], ['editor-only', s.editonly.id, []]]
  for (const [role, id, tags] of assign) {
    const res = await call('admin', 'POST', `/roles/${role}/assign`, { user_id: id, scope_tags: tags })
    if (!res.ok) throw new Error(`assign ${role}: ${res.status} ${await res.text()}`)
  }
  const made = await call('author', 'POST', '/courses', { title: 'Author course', language: 'en', scope_tags: ['author-course'] })
  if (!made.ok) throw new Error(`author course: ${made.status} ${await made.text()}`)
}
