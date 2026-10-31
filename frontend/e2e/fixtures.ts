import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test as base, expect, type BrowserContext, type Page } from '@playwright/test'

export interface Seeded { id: string; email: string; name: string; token: string; csrf: string }
export type Who = 'admin' | 'waiting' | 'student' | 'other' | 'assignee' | 'banme' | 'deleteme' | 'inviteme' | 'author' | 'editonly'

export interface Ids { course: string; editing: string; empty: string; run: string; sources: string; fix: string; live_run: string; conflict: string; dnd: string; publish: string; translated: string; translate_failed: string; no_version: string }

export function ids(): Ids {
  return (JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../../tmp/e2e-sessions.json'), 'utf8')) as { ids: Ids }).ids
}

export function sessions(): Record<Who, Seeded> {
  return JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../../tmp/e2e-sessions.json'), 'utf8')) as Record<Who, Seeded>
}

export async function signInAs(context: BrowserContext, who: Who): Promise<void> {
  await context.addCookies([{ name: 'muse_session', value: sessions()[who].token, url: `http://127.0.0.1:${process.env.E2E_PREVIEW_PORT ?? '4173'}` }])
}

export interface ApiCall { method: string; path: string; csrf: string | undefined; status?: number }

export function recordApi(page: Page): ApiCall[] {
  const calls: ApiCall[] = []
  page.on('request', (req) => {
    const url = new URL(req.url())
    if (url.pathname.startsWith('/api/')) calls.push({ method: req.method(), path: url.pathname, csrf: req.headers()['x-csrf-token'] })
  })
  return calls
}

export const test = base
export { expect }
