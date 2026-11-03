export type ActionKind = 'primary' | 'secondary' | 'destructive' | 'navigation'

export interface ActionMeta {
  id: string
  label: string
  kind: ActionKind
  confirm: boolean
  shortcut: string | null
  where: string[]
}

const a = (id: string, kind: ActionKind, where: string[], extra: { confirm?: boolean; shortcut?: string } = {}): ActionMeta => ({
  id,
  label: `action.${id}`,
  kind,
  confirm: extra.confirm ?? false,
  shortcut: extra.shortcut ?? null,
  where,
})

export const ACTIONS = [
  a('create', 'primary', ['courses', 'roles', 'invites', 'sources', 'publish']),
  a('save', 'primary', ['plan', 'fix', 'roles', 'sources'], { shortcut: 'Ctrl+S' }),
  a('cancel', 'secondary', ['dialogs', 'drawers'], { shortcut: 'Esc' }),
  a('edit', 'secondary', ['roles', 'fix']),
  a('delete', 'destructive', ['plan', 'roles', 'users'], { confirm: true }),
  a('rename', 'secondary', ['plan']),
  a('move-up', 'secondary', ['plan', 'topic']),
  a('move-down', 'secondary', ['plan', 'topic']),
  a('approve', 'primary', ['sources']),
  a('reject', 'destructive', ['sources'], { confirm: false }),
  a('dismiss', 'secondary', ['sources']),
  a('upload', 'primary', ['sources']),
  a('add-address', 'primary', ['sources']),
  a('generate', 'primary', ['generate']),
  a('build-plan', 'primary', ['plan']),
  a('add-module', 'primary', ['plan']),
  a('add-topic', 'secondary', ['plan']),
  a('rewrite', 'secondary', ['fix']),
  a('recheck', 'secondary', ['fix']),
  a('accept', 'secondary', ['fix']),
  a('publish', 'primary', ['publish'], { confirm: true }),
  a('resume', 'primary', ['run']),
  a('stop-run', 'destructive', ['run'], { confirm: true }),
  a('assign', 'secondary', ['users']),
  a('ban', 'destructive', ['users'], { confirm: true }),
  a('unban', 'secondary', ['users'], { confirm: true }),
  a('revoke', 'destructive', ['invites'], { confirm: true }),
  a('copy-link', 'secondary', ['invites', 'publish']),
  a('reload-theirs', 'secondary', ['plan']),
  a('keep-mine', 'secondary', ['plan']),
  a('keep-editing', 'secondary', ['plan', 'roles']),
  a('discard', 'destructive', ['plan', 'roles']),
  a('signout', 'navigation', ['shell', 'waiting']),
  a('signin', 'primary', ['signin', 'invite']),
  a('theme', 'navigation', ['shell']),
  a('notifications', 'navigation', ['shell']),
  a('more', 'navigation', ['users', 'roles']),
  a('shortcuts', 'navigation', ['shell'], { shortcut: '?' }),
  a('retry', 'secondary', ['all states', 'toasts']),
  a('check-again', 'secondary', ['waiting']),
  a('search', 'navigation', ['users'], { shortcut: '/' }),
  a('choose-file', 'secondary', ['sources']),
  a('remove-file', 'secondary', ['sources']),
  a('translate', 'primary', ['publish']),
  a('preview', 'secondary', ['publish', 'preview']),
  a('check-answer', 'primary', ['topic']),
  a('submit-test', 'primary', ['topic']),
  a('retake', 'secondary', ['topic']),
  a('ask', 'primary', ['topic']),
  a('continue', 'primary', ['course outline']),
  a('export', 'secondary', ['report']),
  a('details', 'navigation', ['topic', 'plan', 'sources']),
] as const satisfies readonly ActionMeta[]

export type ActionId = (typeof ACTIONS)[number]['id']

export const ACTION_IDS: readonly string[] = ACTIONS.map((x) => x.id)

export function metaOf(id: ActionId): ActionMeta {
  return ACTIONS.find((x) => x.id === id) as ActionMeta
}

export function shortcutLine(id: ActionId): string | null {
  return metaOf(id).shortcut
}
