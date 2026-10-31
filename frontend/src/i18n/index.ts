import { createI18n } from 'vue-i18n'
import en from './locales/en.json'
import nb from './locales/nb.json'
import uk from './locales/uk.json'

export const LOCALES = ['en', 'nb', 'uk'] as const
export type AppLocale = (typeof LOCALES)[number]
const STORE_KEY = 'sourcebound.locale'

type Tree = { [k: string]: string | Tree }

function bracket(tree: Tree): Tree {
  const out: Tree = {}
  for (const [k, v] of Object.entries(tree)) out[k] = typeof v === 'string' ? `[${v}]` : bracket(v)
  return out
}

export function pickLocale(search: string, stored: string | null, nav: string): string {
  const asked = new URLSearchParams(search).get('lang')
  if (asked === 'pseudo') return 'pseudo'
  if (asked && (LOCALES as readonly string[]).includes(asked)) return asked
  if (stored && (LOCALES as readonly string[]).includes(stored)) return stored
  const short = nav.toLowerCase().slice(0, 2)
  if (short === 'nb' || short === 'no' || short === 'nn') return 'nb'
  if (short === 'uk') return 'uk'
  return 'en'
}

const initial = pickLocale(globalThis.location?.search ?? '', globalThis.localStorage?.getItem(STORE_KEY) ?? null, globalThis.navigator?.language ?? 'en')

export const i18n = createI18n({
  legacy: false,
  locale: initial,
  fallbackLocale: 'en',
  messages: { en, nb, uk, pseudo: bracket(en as Tree) },
  datetimeFormats: Object.fromEntries(
    ['en', 'nb', 'uk', 'pseudo'].map((code) => [code, { short: { dateStyle: 'medium', timeStyle: 'short' } as const }]),
  ),
})

export function setLocale(next: AppLocale): void {
  i18n.global.locale.value = next
  globalThis.localStorage?.setItem(STORE_KEY, next)
  document.documentElement.lang = next
}

document.documentElement.lang = initial === 'pseudo' ? 'en' : initial
