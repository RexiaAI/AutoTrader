export function formatNumber(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(n)
}

export function formatInteger(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(n)
}

export function formatMoney(n: number | null | undefined, currency?: string | null, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  const ccy = currency && currency.trim() ? currency : undefined
  try {
    if (ccy) {
      return new Intl.NumberFormat(undefined, {
        style: 'currency',
        currency: ccy,
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      }).format(n)
    }
  } catch {
    // If the currency code is unknown, fall back to plain number formatting.
  }
  return formatNumber(n, digits)
}

export function formatDateTime(ts: string | null | undefined): string {
  if (!ts) return '—'
  // SQLite timestamps are typically "YYYY-MM-DD HH:MM:SS"
  // We keep it readable and do not assume a specific timezone here.
  return ts.replace('T', ' ')
}




