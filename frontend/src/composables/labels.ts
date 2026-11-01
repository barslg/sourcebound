export function humanize(raw: string): string {
  const words = raw.replace(/[._-]+/g, ' ').trim()
  return words.length === 0 ? raw : words.charAt(0).toUpperCase() + words.slice(1)
}

export function usd(value: string | number | null | undefined): string | null {
  if (value === null || value === undefined) return null
  const n = Number(value)
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : null
}

export function reasonKey(code: string): string {
  return `reason.${code.replace(/\./g, '_')}`
}

export function pluralUk(choice: number): number {
  const n = Math.abs(choice)
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return 1
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return 2
  return 0
}
